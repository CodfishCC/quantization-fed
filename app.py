import streamlit as st
import pandas as pd
import yfinance as yf
from fredapi import Fred
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta

# ==========================================
# 配置区域 (请填入你的 FRED API Key)
# ==========================================
FRED_API_KEY = st.secrets["FRED_API_KEY"]
fred = Fred(api_key=FRED_API_KEY)

# ==========================================
# 1. 数据获取与清洗模块
# ==========================================

@st.cache_data(ttl=3600) # 缓存数据1小时，避免频繁请求
def get_market_data(period='2y'):
    """获取标普500 (SPY) 和 纳指 (QQQ) 的数据"""
    tickers = ['SPY', 'QQQ', '^TNX'] # ^TNX 是10年期美债收益率
    data = yf.download(tickers, period=period, interval='1d')['Close']
    data.columns = ['10Y_Yield', 'QQQ', 'SPY'] # 注意：yfinance列名排序可能不同，需根据实际调整
    # 重新映射列名以防万一
    data = yf.download(tickers, period=period, interval='1d')['Close']
    return data

@st.cache_data(ttl=3600)
def get_fed_liquidity_data(start_date):
    """
    从FRED拉取流动性数据:
    WALCL: 美联储总资产 (Fed Balance Sheet)
    WTREGEN: 财政部TGA账户 (Treasury General Account)
    RRPONTSYD: 逆回购 (Reverse Repo)
    """
    try:
        walcl = fred.get_series('WALCL', observation_start=start_date)
        tga = fred.get_series('WTREGEN', observation_start=start_date)
        rrp = fred.get_series('RRPONTSYD', observation_start=start_date)
        
        df = pd.DataFrame({'Total_Assets': walcl, 'TGA': tga, 'RRP': rrp})
        df = df.fillna(method='ffill') # 填充周末空缺
        
        # 计算净流动性 (单位：十亿美元)
        # Net Liquidity = Fed Assets - TGA - RRP
        df['Net_Liquidity'] = (df['Total_Assets'] - df['TGA'] - df['RRP']) / 1000 
        return df
    except Exception as e:
        st.error(f"FRED 数据拉取失败，请检查API Key。错误信息: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=3600)
def get_rates_stress(start_date):
    """获取 SOFR 和 EFFR 利率"""
    try:
        sofr = fred.get_series('SOFR', observation_start=start_date)
        effr = fred.get_series('EFFR', observation_start=start_date)
        df = pd.DataFrame({'SOFR': sofr, 'EFFR': effr})
        df = df.fillna(method='ffill')
        df['Spread'] = df['SOFR'] - df['EFFR']
        return df
    except Exception as e:
        return pd.DataFrame()

# ==========================================
# 2. 页面布局与可视化模块
# ==========================================

st.set_page_config(page_title="华尔街宏观量化仪表盘", layout="wide")

st.title("🏦 华尔街流动性与风险监控系统")
st.markdown("---")

# 侧边栏控制
st.sidebar.header("设置")
time_range = st.sidebar.selectbox("选择时间范围", ['1年', '2年', '5年'], index=1)
days_map = {'1年': 365, '2年': 730, '5年': 1825}
start_date_str = (datetime.now() - timedelta(days=days_map[time_range])).strftime('%Y-%m-%d')

# 加载数据
with st.spinner('正在从美联储和华尔街拉取最新数据...'):
    market_df = get_market_data(period=f"{days_map[time_range]//365}y")
    liq_df = get_fed_liquidity_data(start_date_str)
    rates_df = get_rates_stress(start_date_str)

# 对齐数据索引 (因为FRED和股市日期可能不完全重合)
combined_df = market_df.join(liq_df, how='inner').join(rates_df, how='inner')

# --- 核心指标概览 ---
col1, col2, col3, col4 = st.columns(4)
if not combined_df.empty:
    latest = combined_df.iloc[-1]
    prev = combined_df.iloc[-2]
    
    col1.metric("标普500 (SPY)", f"${latest['SPY']:.2f}", f"{(latest['SPY']/prev['SPY']-1)*100:.2f}%")
    col2.metric("净流动性 (Net Liquidity)", f"${latest['Net_Liquidity']:.2f} B", f"{(latest['Net_Liquidity'] - prev['Net_Liquidity']):.2f} B")
    col3.metric("10年美债收益率", f"{latest['^TNX']:.2f}%", f"{(latest['^TNX'] - prev['^TNX']):.2f}")
    col4.metric("SOFR - EFFR 利差", f"{latest['Spread']:.2f}", "流动性压力指标")

# --- 标签页视图 ---
tab1, tab2, tab3 = st.tabs(["💧 净流动性模型", "⚖️ 股权风险溢价 (ERP)", "🚨 利率压力监测"])

# === 模型 1: 净流动性 vs 标普500 ===
with tab1:
    st.subheader("美联储净流动性 vs 标普500")
    st.markdown(r"公式: $\text{Net Liquidity} = \text{Fed Balance Sheet} - \text{TGA} - \text{RRP}$")
    
    fig1 = make_subplots(specs=[[{"secondary_y": True}]])
    
    # 绘制净流动性
    fig1.add_trace(
        go.Scatter(x=combined_df.index, y=combined_df['Net_Liquidity'], name="净流动性 (十亿)", line=dict(color='cyan', width=2)),
        secondary_y=False
    )
    
    # 绘制标普500
    fig1.add_trace(
        go.Scatter(x=combined_df.index, y=combined_df['SPY'], name="标普500 (SPY)", line=dict(color='orange', width=2)),
        secondary_y=True
    )
    
    fig1.update_layout(title_text="流动性水位 vs 股市走势", hovermode="x unified", height=500)
    fig1.update_yaxes(title_text="净流动性 (Billion USD)", secondary_y=False)
    fig1.update_yaxes(title_text="SPY 股价", secondary_y=True)
    st.plotly_chart(fig1, use_container_width=True)
    
    st.info("💡 **解读**：当青色线（流动性）大幅下降时，橙色线（股市）通常面临巨大的回调压力。关注TGA账户激增带来的抽水效应。")

# === 模型 2: 股权风险溢价 (ERP) ===
with tab2:
    st.subheader("简易股权风险溢价 (ERP) 模型")
    st.markdown(r"逻辑: 比较 $\frac{1}{PE} \text{ (盈利收益率)}$ 与 $10\text{Y Yield}$")
    
    # 计算简易 ERP: (1 / PE_Ratio) - 10Y_Yield
    # 注意: 这里的PE用静态数据模拟，实际生产环境最好接财报数据API。
    # 这里我们用 SPY的价格倒数作为估值的简单反向代理，或者直接用 Earning Yield (假设PE=25左右作为基准波动)
    # 为了演示，我们简单计算：SPY Earning Yield 估算 = 4% (假设) - 10Y Yield
    
    # 更精确的做法是用 SPY的 EPS 数据。这里我们用 10年美债收益率 vs 纳指走势做负相关对比。
    fig2 = make_subplots(specs=[[{"secondary_y": True}]])
    
    fig2.add_trace(
        go.Scatter(x=combined_df.index, y=combined_df['^TNX'], name="10年美债收益率", line=dict(color='red', width=2)),
        secondary_y=False
    )
    
    fig2.add_trace(
        go.Scatter(x=combined_df.index, y=combined_df['QQQ'], name="纳斯达克100 (QQQ)", line=dict(color='green', width=2)),
        secondary_y=True
    )
    
    # 翻转左侧坐标轴 (收益率越高，越利空)
    fig2.update_yaxes(autorange="reversed", title_text="10年收益率 (逆序)", secondary_y=False)
    fig2.update_yaxes(title_text="QQQ 股价", secondary_y=True)
    
    st.plotly_chart(fig2, use_container_width=True)
    st.warning("⚠️ **注意**：图中红色线（收益率）是**倒序**排列的。如果红线向下插（收益率飙升），绿线（纳指）通常会跟随下跌。")

# === 模型 3: 资金市场压力 (SOFR) ===
with tab3:
    st.subheader("回购市场压力计 (SOFR vs EFFR)")
    
    fig3 = go.Figure()
    fig3.add_trace(go.Scatter(x=combined_df.index, y=combined_df['SOFR'], name='SOFR (担保隔夜利率)'))
    fig3.add_trace(go.Scatter(x=combined_df.index, y=combined_df['EFFR'], name='EFFR (联邦基金利率)', line=dict(dash='dash')))
    
    fig3.update_layout(title="银行间资金成本监控", height=500)
    st.plotly_chart(fig3, use_container_width=True)
    
    st.markdown("""
    **监控逻辑：**
    * 正常情况下，**SOFR** 应该紧贴 **EFFR**。
    * 如果 **SOFR 突然大幅高于 EFFR**（例如本周发债期间），说明市场**缺钱**（抵押品太多，钱太少）。
    * 这通常是股市暴跌的前兆信号。
    """)

# 底部数据展示
with st.expander("查看原始数据"):
    st.dataframe(combined_df.sort_index(ascending=False))