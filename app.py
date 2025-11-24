import streamlit as st
import pandas as pd
import yfinance as yf
from fredapi import Fred
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta

# ==========================================
# 0. 配置与初始化
# ==========================================
st.set_page_config(
    page_title="华尔街宏观仪表盘 (Pro版)", 
    layout="wide", 
    initial_sidebar_state="expanded"
)

# 秘钥获取逻辑 (兼容本地和云端)
try:
    FRED_API_KEY = st.secrets["FRED_API_KEY"]
except:
    # ⚠️如果是本地运行且没有配置 secrets.toml，请直接在这里填入字符串
    FRED_API_KEY = '在此处填入你的FRED_API_KEY' 

try:
    fred = Fred(api_key=FRED_API_KEY)
except:
    st.error("请配置有效的 FRED API Key。")

# ==========================================
# 1. 数据引擎
# ==========================================

@st.cache_data(ttl=3600)
def get_data_bundle(start_date_str):
    """
    核心数据拉取函数
    """
    # --- A. 股市数据 (Yahoo Finance) ---
    tickers = ['SPY', 'QQQ', '^TNX'] 
    try:
        stock_data = yf.download(tickers, start=start_date_str, interval='1d', progress=False)['Close']
        
        # 清洗 MultiIndex 列名问题 (yfinance新版特性)
        if isinstance(stock_data.columns, pd.MultiIndex):
            stock_data.columns = stock_data.columns.get_level_values(0)
        
        # 重命名映射
        mapper = {'^TNX': '10Y_Yield', 'QQQ': 'QQQ', 'SPY': 'SPY'}
        stock_data = stock_data.rename(columns=mapper)
    except Exception as e:
        st.error(f"股市数据拉取失败: {e}")
        return pd.DataFrame()
    
    # --- B. 宏观数据 (FRED) ---
    try:
        # WALCL: 美联储总资产 (周更)
        # WTREGEN: 财政部TGA账户 (日更)
        # RRPONTSYD: 逆回购工具 (日更)
        # SOFR: 担保隔夜融资利率
        # EFFR: 联邦基金有效利率
        
        macro_series = {
            'Total_Assets': fred.get_series('WALCL', observation_start=start_date_str),
            'TGA': fred.get_series('WTREGEN', observation_start=start_date_str),
            'RRP': fred.get_series('RRPONTSYD', observation_start=start_date_str),
            'SOFR': fred.get_series('SOFR', observation_start=start_date_str),
            'EFFR': fred.get_series('EFFR', observation_start=start_date_str)
        }
        macro_df = pd.DataFrame(macro_series)
        
        # 数据清洗：前向填充 (因为美联储资产是周更，需要填满一周)
        macro_df = macro_df.fillna(method='ffill')
        
        # --- C. 模型计算 (核心公式实现) ---
        
        # 1. 净流动性 (Net Liquidity) - 单位换算为十亿美元
        macro_df['Net_Liquidity'] = (macro_df['Total_Assets'] - macro_df['TGA'] - macro_df['RRP']) / 1000
        
        # 2. 资金压力利差 (Spread)
        macro_df['Rate_Spread'] = macro_df['SOFR'] - macro_df['EFFR']
        
        # --- D. 合并数据 ---
        # 仅保留股市交易日的数据 (Inner Join)
        df_final = stock_data.join(macro_df, how='inner').sort_index()
        df_final = df_final.fillna(method='ffill') # 防止个别宏观数据在交易日缺失
        
        return df_final
        
    except Exception as e:
        st.error(f"FRED 宏观数据拉取失败: {e}")
        return pd.DataFrame()

# ==========================================
# 2. 侧边栏交互
# ==========================================

st.sidebar.title("🕹️ 控制台")
st.sidebar.markdown("---")

# 时间选择器
time_options = {
    '1个月 (短线)': 30,
    '3个月 (季度)': 90,
    '6个月 (中期)': 180,
    '今年以来 (YTD)': 'YTD',
    '1年': 365,
    '2年': 730,
    '5年 (长周期)': 1825
}
selected_range = st.sidebar.selectbox("📅 选择回溯周期", list(time_options.keys()), index=4)

# 计算日期
if selected_range == '今年以来 (YTD)':
    start_date = datetime(datetime.now().year, 1, 1)
else:
    days = time_options[selected_range]
    start_date = datetime.now() - timedelta(days=days)

# 格式化为 YYYY-mm-dd
start_date_fmt = start_date.strftime('%Y-%m-%d')
st.sidebar.info(f"数据起始日期: **{start_date_fmt}**")
st.sidebar.caption("提示：在图表上双击可重置缩放，拖拽可放大局部。")

# ==========================================
# 3. 主界面逻辑
# ==========================================

df = get_data_bundle(start_date_fmt)

if not df.empty:
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest
    last_date = df.index[-1].strftime('%Y-%m-%d')

    st.markdown(f"### 📊 华尔街市场概览 (截至 {last_date})")
    
    # --- KPI 看板 (新增 QQQ) ---
    k1, k2, k3, k4, k5 = st.columns(5)
    
    k1.metric("标普500 (SPY)", f"${latest['SPY']:.2f}", f"{(latest['SPY']/prev['SPY']-1)*100:.2f}%")
    k2.metric("纳指100 (QQQ)", f"${latest['QQQ']:.2f}", f"{(latest['QQQ']/prev['QQQ']-1)*100:.2f}%")
    k3.metric("净流动性", f"${latest['Net_Liquidity']:.2f} B", f"{(latest['Net_Liquidity']-prev['Net_Liquidity']):.2f} B")
    k4.metric("10年美债收益率", f"{latest['10Y_Yield']:.2f}%", f"{(latest['10Y_Yield']-prev['10Y_Yield']):.2f}", delta_color="inverse")
    
    # 资金压力报警
    spread = latest['Rate_Spread']
    state = "⚠️ 紧张" if spread > 0.05 else "正常"
    k5.metric("SOFR-EFFR 利差", f"{spread:.3f}%", state, delta_color="off")

    st.markdown("---")

    # ==========================================
    # 模型 A: 净流动性模型
    # ==========================================
    st.subheader("1. 宏观净流动性模型 (Net Liquidity)")
    
    # 公式说明框
    st.info(r"""
    **🔍 计算公式：**
    $$ \text{Net Liquidity} = \text{Fed Balance Sheet (美联储总资产)} - \text{TGA (财政部存款)} - \text{RRP (逆回购余额)} $$
    
    **👉 逻辑：** 剔除掉躺在央行账上不流动的钱（TGA和RRP），剩下的才是真正流向银行体系和金融资产的“活水”。
    """)

    fig_liq = make_subplots(specs=[[{"secondary_y": True}]])

    # 1. 净流动性 (面积图)
    fig_liq.add_trace(
        go.Scatter(
            x=df.index, y=df['Net_Liquidity'], 
            name="净流动性 (十亿美元)",
            fill='tozeroy',
            line=dict(color='rgba(0, 200, 255, 0.5)', width=1),
            fillcolor='rgba(0, 200, 255, 0.1)',
            hovertemplate='%{y:.2f}B<extra></extra>'
        ),
        secondary_y=False
    )

    # 2. 标普500 (线图)
    fig_liq.add_trace(
        go.Scatter(x=df.index, y=df['SPY'], name="标普500 (SPY)", line=dict(color='#ff9f1c', width=2)),
        secondary_y=True
    )
    
    # 3. 纳指QQQ (线图 - 新增)
    fig_liq.add_trace(
        go.Scatter(x=df.index, y=df['QQQ'], name="纳指100 (QQQ)", line=dict(color='#39ff14', width=2)),
        secondary_y=True
    )

    fig_liq.update_layout(
        height=500,
        hovermode="x unified",
        margin=dict(l=20, r=20, t=10, b=20),
        legend=dict(orientation="h", y=1.02, x=0.5, xanchor="center")
    )
    fig_liq.update_xaxes(tickformat="%Y-%m-%d") # 强制X轴日期格式
    fig_liq.update_yaxes(title_text="净流动性 (Billion $)", secondary_y=False, showgrid=False)
    fig_liq.update_yaxes(title_text="股价 (USD)", secondary_y=True, showgrid=True, gridcolor='rgba(128,128,128,0.2)')

    st.plotly_chart(fig_liq, use_container_width=True)

    # ==========================================
    # 下半部分：左右分栏
    # ==========================================
    c1, c2 = st.columns(2)

    # ==========================================
    # 模型 B: 估值压力模型 (ERP Proxy)
    # ==========================================
    with c1:
        st.subheader("2. 利率冲击 vs 纳指")
        st.info(r"""
        **🔍 监控逻辑：** $$ \text{Valuation Risk} \propto \text{Real Yields (实际利率)} $$
        此处使用 **10年期美债收益率 (倒序)** 对比 **QQQ**。
        若红线向下“插水”（收益率飙升），科技股估值通常受压下跌。
        """)
        
        fig_erp = make_subplots(specs=[[{"secondary_y": True}]])
        
        # 10年美债 (倒序)
        fig_erp.add_trace(
            go.Scatter(
                x=df.index, y=df['10Y_Yield'], 
                name="10Y 收益率 (倒序)", 
                line=dict(color='#ff4d4d', width=2)
            ),
            secondary_y=False
        )
        
        # QQQ
        fig_erp.add_trace(
            go.Scatter(x=df.index, y=df['QQQ'], name="QQQ", line=dict(color='#39ff14', width=2)),
            secondary_y=True
        )
        
        fig_erp.update_layout(height=400, hovermode="x unified", margin=dict(t=10, b=20))
        fig_erp.update_xaxes(tickformat="%Y-%m-%d")
        fig_erp.update_yaxes(autorange="reversed", title_text="收益率 %", secondary_y=False, showgrid=False)
        fig_erp.update_yaxes(title_text="QQQ 价格", secondary_y=True)
        
        st.plotly_chart(fig_erp, use_container_width=True)

    # ==========================================
    # 模型 C: 银行间资金压力 (SOFR Stress)
    # ==========================================
    with c2:
        st.subheader("3. 银行间资金压力计")
        st.info(r"""
        **🔍 压力公式：**
        $$ \text{Spread} = \text{SOFR (担保融资利率)} - \text{EFFR (联邦基金利率)} $$
        **预警阈值：** 正常情况下 Spread 应 $\approx 0$ 或微负。若 **Spread > 0.05%**，代表国债抵押品过剩，银行缺钱。
        """)
        
        fig_sofr = go.Figure()
        
        fig_sofr.add_trace(go.Scatter(x=df.index, y=df['SOFR'], name='SOFR', line=dict(color='#00a8cc', width=2)))
        fig_sofr.add_trace(go.Scatter(x=df.index, y=df['EFFR'], name='EFFR', line=dict(color='gray', dash='dash')))
        
        fig_sofr.update_layout(height=400, hovermode="x unified", margin=dict(t=10, b=20))
        fig_sofr.update_xaxes(tickformat="%Y-%m-%d")
        fig_sofr.update_yaxes(title_text="利率 (%)")
        
        st.plotly_chart(fig_sofr, use_container_width=True)

    # 底部页脚
    st.markdown("---")
    st.caption(f"📅 数据最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 数据源: Federal Reserve (FRED) & Yahoo Finance")

else:
    st.warning("⚠️ 正在拉取数据，请稍候... 如果长时间无反应，请检查网络或 API Key 设置。")