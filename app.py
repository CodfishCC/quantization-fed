import streamlit as st
import pandas as pd
import yfinance as yf
from fredapi import Fred
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta

# ==========================================
# 配置区域
# ==========================================
# 优先尝试从 Streamlit Secrets 读取 (云端模式)
# 如果本地运行报错，请直接将下面的字符串替换为你的真实 Key，例如: FRED_API_KEY = 'abcdef12345...'
try:
    FRED_API_KEY = st.secrets["FRED_API_KEY"]
except:
    FRED_API_KEY = '在此处填入你的FRED_API_KEY' 

# 初始化
try:
    fred = Fred(api_key=FRED_API_KEY)
except:
    st.error("请配置有效的 FRED API Key 才能获取宏观数据。")

# 页面宽屏模式
st.set_page_config(page_title="华尔街宏观仪表盘 (PC版)", layout="wide", initial_sidebar_state="expanded")

# ==========================================
# 1. 数据获取模块 (增强版)
# ==========================================

@st.cache_data(ttl=3600)
def get_data_bundle(start_date_str):
    """
    为了提高速度，一次性拉取并对齐所有数据
    """
    # 1. 股市与收益率数据 (Yahoo Finance)
    # yfinance 接收 YYYY-MM-DD 格式
    tickers = ['SPY', 'QQQ', '^TNX'] 
    stock_data = yf.download(tickers, start=start_date_str, interval='1d')['Close']
    # 简单的列名清理
    if isinstance(stock_data.columns, pd.MultiIndex):
        stock_data.columns = stock_data.columns.get_level_values(0)
    
    # 重命名以防万一
    mapper = {'^TNX': '10Y_Yield', 'QQQ': 'QQQ', 'SPY': 'SPY'}
    stock_data = stock_data.rename(columns=mapper)
    
    # 2. 宏观流动性数据 (FRED)
    try:
        # WALCL: 美联储资产 (周更) | WTREGEN: TGA (日更) | RRPONTSYD: 逆回购 (日更)
        walcl = fred.get_series('WALCL', observation_start=start_date_str)
        tga = fred.get_series('WTREGEN', observation_start=start_date_str)
        rrp = fred.get_series('RRPONTSYD', observation_start=start_date_str)
        
        # 利率数据
        sofr = fred.get_series('SOFR', observation_start=start_date_str)
        effr = fred.get_series('EFFR', observation_start=start_date_str)
        
        # 合并宏观数据
        macro_df = pd.DataFrame({
            'Total_Assets': walcl, 
            'TGA': tga, 
            'RRP': rrp,
            'SOFR': sofr,
            'EFFR': effr
        })
        
        # 数据对齐与填充
        # 宏观数据(特别是WALCL)频率低，需要前向填充
        macro_df = macro_df.fillna(method='ffill')
        
        # 计算衍生指标
        # 净流动性 (十亿美元)
        macro_df['Net_Liquidity'] = (macro_df['Total_Assets'] - macro_df['TGA'] - macro_df['RRP']) / 1000
        # 利率压力
        macro_df['Rate_Spread'] = macro_df['SOFR'] - macro_df['EFFR']
        
        # 3. 最终合并
        # 以股市交易日为基准 (inner join 可能导致周末数据丢失，这正是我们想要的，只看交易日)
        df_final = stock_data.join(macro_df, how='inner').sort_index()
        
        # 二次填充，防止某些宏观数据在股市交易日缺失
        df_final = df_final.fillna(method='ffill')
        
        return df_final
        
    except Exception as e:
        st.error(f"FRED 数据拉取失败: {e}")
        return pd.DataFrame()

# ==========================================
# 2. 侧边栏控制
# ==========================================

st.sidebar.header("🕹️ 控制台")

# 时间范围选择 (支持更短周期)
time_options = {
    '1个月': 30,
    '3个月': 90,
    '6个月': 180,
    '今年以来 (YTD)': 'YTD',
    '1年': 365,
    '3年': 1095,
    '5年': 1825
}
selected_range = st.sidebar.selectbox("📅 回溯时间", list(time_options.keys()), index=4)

# 计算开始日期
if selected_range == '今年以来 (YTD)':
    start_date = datetime(datetime.now().year, 1, 1)
else:
    days = time_options[selected_range]
    start_date = datetime.now() - timedelta(days=days)

start_date_str = start_date.strftime('%Y-%m-%d')

st.sidebar.markdown("---")
st.sidebar.info(f"当前数据起始: **{start_date_str}**")

# ==========================================
# 3. 页面布局与逻辑
# ==========================================

# 加载数据
df = get_data_bundle(start_date_str)

if not df.empty:
    latest = df.iloc[-1]
    # 尝试获取前一个交易日数据用于计算变动，防止数据太少报错
    if len(df) > 1:
        prev = df.iloc[-2]
    else:
        prev = latest

    # --- 顶栏：关键指标 KPI ---
    st.markdown("### 📊 市场核心看板")
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    
    kpi1.metric(
        "标普500 (SPY)", 
        f"${latest['SPY']:.2f}", 
        f"{(latest['SPY']/prev['SPY']-1)*100:.2f}%",
        delta_color="normal"
    )
    kpi2.metric(
        "美联储净流动性", 
        f"${latest['Net_Liquidity']:.2f} B", 
        f"{(latest['Net_Liquidity'] - prev['Net_Liquidity']):.2f} B",
        help="Fed资产负债表 - TGA - RRP"
    )
    kpi3.metric(
        "10年美债收益率", 
        f"{latest['10Y_Yield']:.2f}%", 
        f"{(latest['10Y_Yield'] - prev['10Y_Yield']):.2f}",
        delta_color="inverse" # 收益率涨通常是坏事，显示红色
    )
    
    # 智能判断 SOFR 状态
    spread_val = latest['Rate_Spread']
    spread_color = "normal" if spread_val < 0.05 else "inverse" # 利差过大显示红色警告
    kpi4.metric(
        "资金压力 (SOFR-EFFR)", 
        f"{spread_val:.3f}%", 
        "正常" if spread_val < 0.05 else "⚠️ 紧张",
        delta_color="off"
    )

    st.markdown("---")

    # --- 第一行：核心主图 (流动性 vs 股市) ---
    # PC端这幅图最重要，给予整行宽度
    
    st.subheader("💧 宏观流动性驱动模型")
    
    fig_liq = make_subplots(specs=[[{"secondary_y": True}]])
    
    # 区域图显示流动性
    fig_liq.add_trace(
        go.Scatter(
            x=df.index, y=df['Net_Liquidity'], 
            name="净流动性 (Net Liquidity)", 
            fill='tozeroy', # 填充背景，视觉更强
            line=dict(color='rgba(0, 255, 255, 0.5)', width=1),
            fillcolor='rgba(0, 255, 255, 0.1)'
        ),
        secondary_y=False
    )
    
    # 线条显示标普
    fig_liq.add_trace(
        go.Scatter(x=df.index, y=df['SPY'], name="标普500 (SPY)", line=dict(color='#ff9f1c', width=2)),
        secondary_y=True
    )
    
    fig_liq.update_layout(
        height=450, 
        margin=dict(l=20, r=20, t=30, b=20),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    fig_liq.update_yaxes(title_text="净流动性 (十亿 $)", secondary_y=False, showgrid=False)
    fig_liq.update_yaxes(title_text="标普500点位", secondary_y=True, showgrid=True, gridcolor='rgba(128,128,128,0.2)')
    
    st.plotly_chart(fig_liq, use_container_width=True)

    # --- 第二行：左右分栏 (ERP估值 和 资金压力) ---
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("📉 纳指 vs 利率 (倒序)")
        st.caption("红线向下代表收益率飙升，通常压制纳指")
        
        fig_erp = make_subplots(specs=[[{"secondary_y": True}]])
        
        # 10年期美债 (倒序)
        fig_erp.add_trace(
            go.Scatter(x=df.index, y=df['10Y_Yield'], name="10年美债 (倒序)", line=dict(color='#ff595e', width=2)),
            secondary_y=False
        )
        
        # 纳指
        fig_erp.add_trace(
            go.Scatter(x=df.index, y=df['QQQ'], name="纳指100 (QQQ)", line=dict(color='#8ac926', width=2)),
            secondary_y=True
        )
        
        # 关键：翻转左侧坐标轴
        fig_erp.update_yaxes(autorange="reversed", title_text="收益率 %", secondary_y=False, showgrid=False)
        fig_erp.update_yaxes(title_text="QQQ 股价", secondary_y=True)
        fig_erp.update_layout(height=400, hovermode="x unified", margin=dict(l=10, r=10, t=30, b=20))
        
        st.plotly_chart(fig_erp, use_container_width=True)

    with col_right:
        st.subheader("🚨 资金市场压力 (SOFR)")
        st.caption("蓝线若大幅偏离虚线，提示流动性枯竭风险")
        
        fig_sofr = go.Figure()
        
        fig_sofr.add_trace(go.Scatter(x=df.index, y=df['SOFR'], name='SOFR', line=dict(color='#1982c4', width=2)))
        fig_sofr.add_trace(go.Scatter(x=df.index, y=df['EFFR'], name='EFFR (基准)', line=dict(color='gray', dash='dash')))
        
        fig_sofr.update_layout(height=400, hovermode="x unified", margin=dict(l=10, r=10, t=30, b=20))
        fig_sofr.update_yaxes(title_text="利率 %")
        
        st.plotly_chart(fig_sofr, use_container_width=True)

    # --- 底部数据源说明 ---
    st.caption(f"数据来源: Federal Reserve (FRED) & Yahoo Finance | 更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

else:
    st.warning("暂无数据，请检查网络连接或 API Key 设置。")