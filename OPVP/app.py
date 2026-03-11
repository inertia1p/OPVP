
# app.py
# 运行方式：streamlit run app.py
# 依赖安装：pip install streamlit pandas plotly openpyxl

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import datetime
import random
import io
import os
import requests
import json
from openai import OpenAI

# 页面配置
st.set_page_config(page_title="公司欠费数据可视化平台", layout="wide", initial_sidebar_state="collapsed")

# 登录验证函数
def check_password():
    """如果用户输入了正确的密码，则返回 True。"""
    # 检查 st.secrets 是否已配置，如果没有配置则提示开发者
    if "passwords" not in st.secrets:
        st.error("⚠️ 未在 Streamlit Secrets 中配置密码。如果是本地运行，请确保 `.streamlit/secrets.toml` 存在。")
        st.stop()

    def password_entered():
        """检查用户输入的密码是否正确。"""
        if st.session_state["password"] == st.secrets["passwords"]["admin"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # 不存储密码
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        # 第一次访问，显示输入框
        st.markdown("""
            <style>
            .login-container {
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                height: 60vh;
            }
            </style>
            """, unsafe_allow_html=True)
        st.title("🔐 公司欠费数据可视化平台")
        st.text_input(
            "请输入访问授权码", type="password", on_change=password_entered, key="password"
        )
        if "password_correct" in st.session_state and not st.session_state["password_correct"]:
            st.error("😕 密码错误，请重试。")
        st.stop()
        return False
    elif not st.session_state["password_correct"]:
        # 密码错误，再次显示输入框
        st.title("🔐 公司欠费数据可视化平台")
        st.text_input(
            "请输入访问授权码", type="password", on_change=password_entered, key="password"
        )
        st.error("😕 密码错误，请重试。")
        st.stop()
        return False
    else:
        # 密码正确
        return True

# 身份验证
if check_password():

    # 初始化 session_state
    if "use_uploaded" not in st.session_state:
        st.session_state.use_uploaded = False
    if "uploaded_data" not in st.session_state:
        st.session_state.uploaded_data = None

# 左上角经营单元筛选
business_units = ["全市", "本级企业", "本级政府", "禾城", "嘉善", "平湖", "海盐", "海宁", "桐乡", "濮院","政企业务群"]
selected_unit = st.selectbox("经营单元", business_units, index=0, key="unit_filter")

# 数据文件路径
DEFAULT_DATA_FILE = "历年汇总/2025-2026汇总.xlsx"

# 实际数据加载函数
@st.cache_data
def load_data(unit="全市"):
    if not os.path.exists(DEFAULT_DATA_FILE):
        st.error(f"未找到汇总数据文件: {DEFAULT_DATA_FILE}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    df = pd.read_excel(DEFAULT_DATA_FILE)
    df['账期'] = df['账期'].astype(str)
    
    # 1. 过滤责任单元
    if unit != "全市":
        filtered_df = df[df['责任单元'] == unit].copy()
    else:
        filtered_df = df.copy()

    # 最新账期（用于汇总数据图表）
    latest_cycle_unit = df['账期'].max()
    latest_unit_data = filtered_df[filtered_df['账期'] == latest_cycle_unit]
    
    # 2. top_df: 最新账期的各集团客户排名 (用于图 1-4)
    # 使用相对路径并统一使用正斜杠以兼容 Linux/Streamlit Cloud
    GROUP_DATA_FILE = "历年汇总/2025-2026集团汇总.xlsx"
    if not os.path.exists(GROUP_DATA_FILE):
        st.error(f"未找到集团客户数据文件: {GROUP_DATA_FILE}")
        top_df = pd.DataFrame(columns=["客户", "责任单元", "累计欠费", "累计坏账", "信用减值", "一年以上账龄", "备注"])
    else:
        group_df = pd.read_excel(GROUP_DATA_FILE)
        group_df['账期'] = group_df['账期'].astype(str)
        latest_cycle_group = group_df['账期'].max()

        # 根据经营单元筛选集团数据
        if unit == "全市":
            top_data = group_df[group_df['账期'] == latest_cycle_group].copy()
        else:
            top_data = group_df[(group_df['账期'] == latest_cycle_group) & (group_df['经营单元'] == unit)].copy()

        top_df = top_data.rename(columns={"月初欠费": "累计欠费", "集团名": "客户"})
        
        # 剔除非集团客户/用户
        top_df = top_df[~top_df["客户"].isin(["非集团客户", "非集团用户"])].copy()
        
        top_df["责任单元"] = top_df["经营单元"]  # 适配原有 UI 的“责任单元”字段

        # 确保列齐全 (图 1-4 核心列)
        age_cols = ["1个月内", "2-3个月", "3-6个月", "7-12个月", "1年以上"]
        for col in ["客户", "责任单元", "累计欠费", "累计坏账", "信用减值"] + age_cols:
            if col not in top_df.columns:
                top_df[col] = 0
        top_df["一年以上账龄"] = top_df["1年以上"]
        top_df["备注"] = ""
        top_df = top_df.sort_values(by="累计欠费", ascending=False).reset_index(drop=True)

    # 3. age_df: 账龄数据 (用于图 5)
    # 按账期从 DEFAULT_DATA_FILE 聚合
    age_cols = ["1个月内", "2-3个月", "3-6个月", "7-12个月", "1年以上"]
    age_df = filtered_df.groupby("账期")[age_cols].sum().reset_index()
    
    # 4. product_df: 分产品欠费（最新账期，用于图 6）
    # 从 DEFAULT_DATA_FILE 聚合最新账期数据
    product_cols = ["手机", "宽带", "专线", "连接", "IDC", "标品（不含连接）"]
    if not latest_unit_data.empty:
        prod_data = latest_unit_data[product_cols].sum().reset_index()
        prod_data.columns = ["产品类型", "欠费金额"]
        product_df = prod_data
    else:
        product_df = pd.DataFrame(columns=["产品类型", "欠费金额"])

    # 5. trend_df: 趋势数据
    if unit == "全市":
        # 全市：先汇总各单元已含占收比的数据，反推全市年化收入，再算全市占收比
        city_df = filtered_df.groupby("账期").agg({
            "月初欠费": "sum",
            "累计坏账": "sum",
            "信用减值": "sum",
            "占收比": "mean"  # 占位，先保留字段
        }).reset_index()
        # 用各单元“月初欠费 / 占收比”求和得全市年化收入
        # 仅保留占收比>0的行，避免除0
        calc_df = filtered_df[filtered_df["占收比"] > 0].copy()
        calc_df["年化收入"] = calc_df["月初欠费"] / (calc_df["占收比"] / 100)
        city_annual_income = calc_df.groupby("账期")["年化收入"].sum()
        city_arrears = city_df["月初欠费"]
        # 按账期对齐，确保index一致
        city_df["占收比"] = (city_arrears / city_annual_income.reindex(city_df["账期"]).values * 100).fillna(0)
        trend_df = city_df.rename(columns={"账期": "年月", "月初欠费": "累计欠费"})
    else:
        # 非全市：直接按账期汇总，占收比字段已存在
        trend_df = filtered_df.groupby("账期").agg({
            "月初欠费": "sum",
            "累计坏账": "sum",
            "信用减值": "sum",
            "占收比": "mean"
        }).reset_index()
        trend_df.rename(columns={"账期": "年月", "月初欠费": "累计欠费"}, inplace=True)

    # 兜底：若占收比仍缺失则补0
    if "占收比" not in trend_df.columns:
        trend_df["占收比"] = 0
    
    return top_df, age_df, product_df, trend_df

# 模块切换
with st.sidebar:
    st.header("模块切换")
    module = st.radio("选择模块", ["模块1: 抓手", "模块2: 核心指标", "模块3: AI 风险评价", "模块4: 数据上传"], label_visibility="collapsed")

# 数据读取工具函数
@st.cache_data
def load_data_from_excel(file):
    xls = pd.ExcelFile(file)
    sheets = {
        "客户Top": "top_df",
        "账龄": "age_df",
        "产品": "product_df",
        "趋势": "trend_df"
    }
    data = {}
    for sheet_name, key in sheets.items():
        if sheet_name in xls.sheet_names:
            data[key] = xls.parse(sheet_name)
        else:
            data[key] = None
    return data

# 模块3: 数据上传
if module == "模块3: 数据上传":
    st.title("模块3: 数据上传")
    st.markdown("上传 Excel 后，将覆盖默认的汇总数据。")
    uploaded_file = st.file_uploader("选择Excel文件", type=["xlsx"])
    if uploaded_file is not None:
        try:
            data = load_data_from_excel(uploaded_file)
            st.session_state.uploaded_data = data
            st.session_state.use_uploaded = True
            st.success("上传成功！请切换到其他模块查看。")
        except Exception as e:
            st.error(f"解析失败: {e}")
    
    if st.button("恢复默认数据"):
        st.session_state.use_uploaded = False
        st.session_state.uploaded_data = None
        st.rerun()

# 初始化数据
if st.session_state.use_uploaded and st.session_state.uploaded_data is not None:
    data = st.session_state.uploaded_data
    top_df = data["top_df"] if data["top_df"] is not None else load_data(selected_unit)[0]
    age_df = data["age_df"] if data["age_df"] is not None else load_data(selected_unit)[1]
    product_df = data["product_df"] if data["product_df"] is not None else load_data(selected_unit)[2]
    trend_df = data["trend_df"] if data["trend_df"] is not None else load_data(selected_unit)[3]
else:
    top_df, age_df, product_df, trend_df = load_data(selected_unit)

# 模块1: 抓手
if module == "模块1: 抓手":
    st.title("模块1: 抓手")

    # 创建左右布局：左侧 70% 用于图表，右侧 30% 用于显示流程规范
    col_main, col_info = st.columns([7, 3])

    with col_main:
        # 统一 TOPN 输入控件
        max_top = len(top_df) if len(top_df) > 0 else 10
        top_n = st.number_input("查询 Top N 客户", min_value=1, max_value=max_top, value=min(10, max_top), step=1, key="topn_input")

        # 图1: 累计欠费的分账龄欠费堆叠（堆叠条形图，按客户展示各账龄区间欠费构成）
        st.subheader("图1: 累计欠费的分账龄欠费堆叠")
        # 取 Top N 客户
        topn_customers = top_df.nlargest(top_n, "累计欠费")["客户"].tolist()
        # 从 top_df 中筛选这些客户，并保留账龄列
        stack_df = top_df[top_df["客户"].isin(topn_customers)].copy()
        age_cols = ["1个月内", "2-3个月", "3-6个月", "7-12个月", "1年以上"]
        # 确保账龄列存在，缺失补0
        for col in age_cols:
            if col not in stack_df.columns:
                stack_df[col] = 0
        # 转换为万元并保留两位小数
        stack_df[age_cols] = (stack_df[age_cols] / 10000).round(2)
        # 宽表转长表
        stack_melt = stack_df.melt(id_vars=["客户"], value_vars=age_cols,
                                   var_name="账龄区间", value_name="欠费金额(万元)")
        # 自定义色序
        color_map = {
            "1个月内": "#2A9D8F",
            "2-3个月": "#E9C46A",
            "3-6个月": "#F4A261",
            "7-12个月": "#E76F51",
            "1年以上": "#E63946"
        }
        fig_stack_bar = px.bar(stack_melt, y="客户", x="欠费金额(万元)", color="账龄区间",
                               orientation="h", title=f"累计欠费分账龄构成 - Top {top_n}",
                               color_discrete_map=color_map,
                               category_orders={"账龄区间": age_cols},
                               text="欠费金额(万元)")
        fig_stack_bar.update_layout(yaxis={"categoryorder": "total ascending"})
        fig_stack_bar.update_traces(texttemplate='%{text:.2f}', textposition='inside')
        st.plotly_chart(fig_stack_bar, use_container_width=True)

        # 图2-4: Top N 客户排行（条形图 + 可编辑表格）
        tops = {
            "图2: 累计坏账TOP情况": "累计坏账",
            "图3: 信用减值TOP情况": "信用减值",
            "图4: 一年以上账龄情况": "一年以上账龄"
        }

        for title, col in tops.items():
            st.subheader(title)
            topn_df = top_df.nlargest(top_n, col)[["客户", "责任单元", col, "备注"]].copy()
            
            # 转换为万元并保留两位小数
            new_col_name = f"{col}(万元)"
            topn_df[new_col_name] = (topn_df[col] / 10000).round(2)

            # 条形图
            fig_bar = px.bar(topn_df, x=new_col_name, y="客户", orientation="h", title=f"{title} - Top {top_n}",
                             color=new_col_name, text=new_col_name)
            fig_bar.update_layout(yaxis={"categoryorder": "total ascending"})
            fig_bar.update_traces(texttemplate='%{text:.2f}', textposition='outside')
            st.plotly_chart(fig_bar, use_container_width=True)
            
            # 可编辑表格（备注）
            st.write(f"Top {top_n} 客户表格（可编辑备注说明欠费原因）")
            # 仅展示转换后的万元列和备注
            display_df = topn_df[["客户", "责任单元", new_col_name, "备注"]]
            edited_df = st.data_editor(display_df, num_rows="dynamic", use_container_width=True, key=f"editor_{title}")

            # 保存按钮（为每个图生成唯一 key）
            if st.button("保存备注", key=f"save_notes_{title}"):
                # 生成修改日志
                log_entry = f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - 用户修改备注\n"
                for idx, row in edited_df.iterrows():
                    orig = top_df.loc[top_df["客户"] == row["客户"], "备注"].values
                    orig_note = orig[0] if len(orig) > 0 else ""
                    if orig_note != row["备注"]:
                        log_entry += f"  客户【{row['客户']}】备注: '{orig_note}' -> '{row['备注']}'\n"

                # 保存到本地文件（按客户维度覆盖）
                edited_df.to_csv("customer_notes.csv", index=False, encoding="utf-8")
                # 追加日志
                with open("edit_log.txt", "a", encoding="utf-8") as f:
                    f.write(log_entry)
                st.success("备注已保存，修改日志已记录！")

                # 更新内存缓存，下次生成时优先读取
                top_df.update(edited_df)

    with col_info:
        st.subheader("具体催缴措施及工作流程")
        
        # 准备表格数据，对“欠费账龄”进行视觉上的合并处理（除首行外留空）
        workflow_data = {
            "欠费账龄": [
                "出帐当月及次月 (逾期1个月)", "", "", "",
                "短期欠费 (逾期2－6个月)", "",
                "长期欠费 (逾期7-12个月)", "", "", ""
            ],
            "催缴动作要求": [
                "电话提醒", "邮件提醒", "线上消息提醒", "上门提醒",
                "发函提醒", "分管副总或总经理上门催缴",
                "二次发函提醒", "发送律师函", "诉讼或仲裁", "经营单元内部会议评估记录"
            ],
            "具体工作流程": [
                "§ 客户经理启动提醒还款流程，可通过电话、邮件或上门拜访方式进行。",
                "§ 客户产生逾期欠费之日起1个月内，客户经理启动提醒还款流程。",
                "§ 政企业务群下发欠费清单至各营销单元落实责任人。",
                "-",
                "§ 产生逾期欠费之日起2个月内，由客户经理寄送公司催款函。",
                "§ 填写《用印审批单》，报政企业务群审核后执行报停处理。",
                "§ 逾期账期超过6个月，应召开会议评估，并发送律师函。",
                "§ 针对逾期欠费金额达5000元及以上的，发送律师函。",
                "§ ICT业务应根据项目的合同约定确定寄送律师函时间。",
                "-"
            ],
            "工作记录要求": [
                "电话录音；", "邮件截图；", "上门催缴照片；", "客户经理上门拜访记录；",
                "第一次催款函回执联；", "分管副总/总经理上门记录；",
                "第二次催款函及挂号信单据；",
                "合同、发票、律师函申请等；",
                "律师函及其快递送达单据；",
                "诉讼申请/会议评估记录；"
            ]
        }
        
        df_workflow = pd.DataFrame(workflow_data)
        
        # 使用 dataframe 展示，隐藏索引
        st.dataframe(df_workflow, use_container_width=True, hide_index=True)

        # 添加文件下载功能
        doc_path = r"关于加强政企线应收账款管控 明确催缴动作要求的通知.docx"
        if os.path.exists(doc_path):
            with open(doc_path, "rb") as f:
                btn = st.download_button(
                    label="📄 下载：完整工作要求文件 (Docx)",
                    data=f,
                    file_name="关于加强政企线应收账款管控 明确催缴动作要求的通知.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
        else:
            st.error(f"未找到相关文件：{os.path.basename(doc_path)}")
        
        st.info("""
        💡 **温馨提示**：
        请各经营单元严格按照上述流程开展催缴工作，确保工作记录完整存档。
        """)

# 模块2: 核心指标
elif module == "模块2: 核心指标":
    st.title("模块2: 核心指标")
    
    # 板块1
    st.header("板块1")
    
    # 图5: 账龄情况 - 堆叠柱状图
    st.subheader("图5: 账龄情况")
    # 自定义色序，账龄越长颜色越严重
    color_map = {
        "1个月内": "#2A9D8F",
        "2-3个月": "#E9C46A",
        "3-6个月": "#F4A261",
        "7-12个月": "#E76F51",
        "1年以上": "#E63946"
    }
    # 将账期转为字符串，确保格式为年月，如202501代表2025年1月
    age_df["账期"] = age_df["账期"].astype(str)
    # 读取系统年份，优先显示上一年至本年的数据
    current_year = datetime.datetime.now().year
    start_period = f"{current_year - 1}01"
    end_period = f"{current_year}12"
    # 过滤账期范围
    age_df_filtered = age_df[
        (age_df["账期"] >= start_period) & (age_df["账期"] <= end_period)
    ]
    # 将账期格式从 202501 转为 2025年5月 用于展示
    age_df_filtered["账期-年月"] = age_df_filtered["账期"].str[:4] + "年" + age_df_filtered["账期"].str[4:6] + "月"
    # 将欠费金额从元转为万元
    age_cols = ["1个月内", "2-3个月", "3-6个月", "7-12个月", "1年以上"]
    age_df_filtered[age_cols] = age_df_filtered[age_cols] / 10000
    age_melt = age_df_filtered.melt(id_vars=["账期", "账期-年月"], var_name="账龄区间", value_name="欠费金额(万元)")
    fig_stack = px.bar(
        age_melt,
        x="账期-年月",
        y="欠费金额(万元)",
        color="账龄区间",
        title="账龄结构堆叠柱状图（重点观察红色“1年以上”占比变化）",
        barmode="stack",
        category_orders={"账龄区间": ["1个月内", "2-3个月", "3-6个月", "7-12个月", "1年以上"]},
        color_discrete_map=color_map,
        text="欠费金额(万元)"  # 指定显示“欠费金额”作为标签
    )
    # 直接显示数值，禁用科学计数法
    fig_stack.update_traces(texttemplate='%{text:.2f}', textposition='outside')

    fig_stack.update_layout(
        xaxis_title="欠费账期",
        yaxis_title="欠费金额(万元)",
        legend_title="账龄区间"
    )
    st.plotly_chart(fig_stack, use_container_width=True)
    # 图6: 分产品欠费情况 - 柱状图
    st.subheader("图6: 分产品欠费情况")
    # 将金额转为万元
    product_df["欠费金额(万元)"] = product_df["欠费金额"] / 10000
    fig_product = px.bar(product_df, x="产品类型", y="欠费金额(万元)",
                         title="分产品欠费柱状图", color="欠费金额(万元)", text="欠费金额(万元)")
    # 禁用科学计数法，保留两位小数
    fig_product.update_traces(texttemplate='%{text:.2f}', textposition='outside')
    fig_product.update_layout(yaxis_title="欠费金额(万元)")
    st.plotly_chart(fig_product, use_container_width=True)
    
    # 板块2: 趋势（筛选单元）
    st.header("板块2: 趋势")
    # 默认年月范围为2025年1月-2026年12月
    default_start = datetime.datetime(2025, 1, 1)
    default_end = datetime.datetime(2026, 12, 31)
    start_date, end_date = st.date_input("选择年月范围", [default_start, default_end])

    # 将“年月”列统一转为字符串格式，形如 2024-12
    trend_df["年月"] = trend_df["年月"].astype(str)
    # 若原始为 202412 格式，则先转为 2024-12
    if trend_df["年月"].str.len().iloc[0] == 6:
        trend_df["年月"] = trend_df["年月"].str[:4] + "-" + trend_df["年月"].str[4:6]

    # 构造用于筛选的字符串
    start_str = start_date.strftime("%Y-%m")
    end_str   = end_date.strftime("%Y-%m")

    filtered_trend = trend_df[
        (trend_df["年月"] >= start_str) & (trend_df["年月"] <= end_str)
    ].copy()

    # 将“年月”转为中文格式：2025-05 -> 2025年5月
    def fmt_ym(ym):
        y, m = ym.split("-")
        return f"{y}年{int(m)}月"

    filtered_trend["年月"] = filtered_trend["年月"].apply(fmt_ym)

    # 图7: 累计欠费趋势
    st.subheader("图7: 累计欠费趋势")
    # 金额转万元
    filtered_trend["累计欠费(万元)"] = filtered_trend["累计欠费"] / 10000
    fig_arrear = px.line(filtered_trend, x="年月", y="累计欠费(万元)", title="累计欠费折线图", markers=True, text="累计欠费(万元)")
    # 禁用科学计数法
    fig_arrear.update_layout(yaxis=dict(tickformat=',.2f'))
    fig_arrear.update_traces(texttemplate='%{text:.2f}', textposition='top center')
    st.plotly_chart(fig_arrear, use_container_width=True)

    # 图8: 累计坏账趋势
    st.subheader("图8: 累计坏账趋势")
    # 金额转万元
    filtered_trend["累计坏账(万元)"] = filtered_trend["累计坏账"] / 10000
    fig_bad = px.line(filtered_trend, x="年月", y="累计坏账(万元)", title="累计坏账折线图", markers=True, text="累计坏账(万元)")
    fig_bad.update_layout(yaxis=dict(tickformat=',.2f'))
    fig_bad.update_traces(texttemplate='%{text:.2f}', textposition='top center')
    st.plotly_chart(fig_bad, use_container_width=True)

    # 图9: 信用减值趋势
    st.subheader("图9: 信用减值趋势")
    # 金额转万元
    filtered_trend["信用减值(万元)"] = filtered_trend["信用减值"] / 10000
    fig_credit = px.line(filtered_trend, x="年月", y="信用减值(万元)", title="信用减值折线图", markers=True, text="信用减值(万元)")
    fig_credit.update_layout(yaxis=dict(tickformat=',.2f'))
    fig_credit.update_traces(texttemplate='%{text:.2f}', textposition='top center')
    st.plotly_chart(fig_credit, use_container_width=True)

    # 图10: 占收比趋势  
    st.subheader("图10: 占收比趋势")
    fig_ratio = px.line(filtered_trend, x="年月", y="占收比", title="占收比折线图", markers=True, text="占收比")
    # 占收比不是金额，保持原样，禁用科学计数法，以百分比显示
    fig_ratio.update_layout(yaxis=dict(tickformat='.2%'))
    fig_ratio.update_traces(texttemplate='%{text:.2%}', textposition='top center')
    st.plotly_chart(fig_ratio, use_container_width=True)
# 模块3: AI 风险评价
elif module == "模块3: AI 风险评价":
    st.title("模块3: AI 风险评价")
    st.markdown(f"### 针对经营单元：**{selected_unit}** 的风险评估报告")

    # 1. 准备评价数据
    # 获取最新账期的核心指标
    latest_top = top_df.copy()
    total_arrears = latest_top["累计欠费"].sum()
    total_bad_debt = latest_top["累计坏账"].sum()
    total_credit_impairment = latest_top["信用减值"].sum()
    
    # 重点客户情况 (提取 Top 10)
    top_arrears_customers = latest_top.nlargest(10, "累计欠费")[["客户", "累计欠费", "一年以上账龄"]]
    top_long_age_customers = latest_top.nlargest(10, "一年以上账龄")[["客户", "一年以上账龄"]]
    top_bad_debt_customers = latest_top.nlargest(10, "累计坏账")[["客户", "累计坏账"]]
    
    # 构造客户列表字符串
    arrears_cust_str = "\n".join([f"- {row['客户']}: 欠费 {row['累计欠费']/10000:.2f} 万元 (其中1年以上 {row['一年以上账龄']/10000:.2f} 万元)" for _, row in top_arrears_customers.iterrows()])
    long_age_cust_str = "\n".join([f"- {row['客户']}: 1年以上账龄 {row['一年以上账龄']/10000:.2f} 万元" for _, row in top_long_age_customers.iterrows()])
    bad_debt_cust_str = "\n".join([f"- {row['客户']}: 累计坏账 {row['累计坏账']/10000:.2f} 万元" for _, row in top_bad_debt_customers.iterrows()])

    # 账龄结构 (最新账期)
    latest_age = age_df.iloc[-1] if not age_df.empty else pd.Series()
    age_cols = ["1个月内", "2-3个月", "3-6个月", "7-12个月", "1年以上"]
    total_age_sum = latest_age[age_cols].sum() if not latest_age.empty and latest_age[age_cols].sum() > 0 else 0
    long_term_ratio = (latest_age["1年以上"] / total_age_sum) * 100 if total_age_sum > 0 else 0
    
    # 趋势分析 (最近三个月)
    recent_trend = trend_df.tail(3)
    arrears_growth = ((recent_trend["累计欠费"].iloc[-1] / recent_trend["累计欠费"].iloc[0]) - 1) * 100 if len(recent_trend) >= 2 and recent_trend["累计欠费"].iloc[0] > 0 else 0

    # 2. 构建 AI 提示词（Prompt）
    prompt = f"""
    你是一位资深通信行业财务风险管理专家。
    请基于以下【{selected_unit}】经营单元的深度数据，生成一份结构化、专业且重深度分析的欠费风险评估报告。

    ### 格式要求 (非常重要)：
    1. **数字高亮**：报告中出现的所有金额、百分比、账期等数字，必须使用 Markdown 的加粗语法，例如 **123.45**。
    2. **高风险标红**：点名提到的“重点风险客户”名称，必须使用 :red[客户名称] 的语法进行标红。
    3. **内容偏重**：报告应“重分析、轻建议”。深度剖析欠费成因、客户违约迹象、指标背后的经营逻辑，管理建议需精简且极具针对性。

    ### 待分析数据：
    #### 1. 全局核心指标
    - 累计欠费总额：{total_arrears/10000:.2f} 万元
    - 累计坏账总额：{total_bad_debt/10000:.2f} 万元
    - 本期信用减值压力：{total_credit_impairment/10000:.2f} 万元
    - 占收比水平：{recent_trend['占收比'].iloc[-1] if not recent_trend.empty else 0:.2f}%
    - 近三月趋势：{'上升' if arrears_growth > 0 else '下降'} {abs(arrears_growth):.1f}%

    #### 2. 账龄结构现状
    - 1 个月内 (正常波动区)：{latest_age.get('1个月内', 0)/10000:.2f} 万元
    - 1 年以上 (极高风险区)：{latest_age.get('1年以上', 0)/10000:.2f} 万元
    - 长账龄占比：{long_term_ratio:.1f}%

    #### 3. 重点客户数据 (Top 10)
    【累计欠费前十】：
    {arrears_cust_str}

    【长账龄风险前十】：
    {long_age_cust_str}

    【累计坏账前十】：
    {bad_debt_cust_str}

    ### 报告输出大纲：
    1. **风险综合评价**：判定风险等级（极高/高/中/低），结合指标深度解读判定理由。
    2. **核心指标深度分析**：结合趋势、账龄占比、坏账规模，深度剖析该单元目前的财务健康状态。
    3. **突出风险客户穿透**：综合以上三个 Top 10 列表，点名指出 3-5 个问题最为突出的客户，分析其可能的违约特征（如金额巨大且全部为长账龄，或坏账持续攀升等）。
    4. **精简管理建议**：给出 2-3 条针对性极强的管控动作，必须明确指出应优先处理哪些高风险客户。
    """

    # 3. 使用 OpenAI 官方 SDK 方式接入 DeepSeek 免费模型
    # 从 st.secrets 安全获取 API 密钥
    deepseek_api_key = st.secrets.get("deepseek_api_key")
        
    # 初始化 DeepSeek 客户端
    client = OpenAI(
        api_key=deepseek_api_key,
        base_url="https://ark.cn-beijing.volces.com/api/v3"
    )

    def call_llm(prompt: str) -> str:
        """通用 LLM 调用函数（DeepSeek 免费模型 deepseek-chat）"""
        if not deepseek_api_key:
            return "⚠️ 请在 secrets.toml 中配置 API Key 后重试。"

        try:
            response = client.chat.completions.create(
                model="deepseek-v3-2-251201",  # 免费模型
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"LLM 调用异常：{e}"

    # 4. 前端交互
    if st.button("生成 AI 风险评价报告", type="primary"):
        with st.spinner("AI 正在深度分析经营数据，请稍候..."):
            analysis_report = call_llm(prompt)
            st.markdown("---")
            st.markdown(analysis_report)

            st.download_button(
                label="下载分析报告 (TXT)",
                data=analysis_report,
                file_name=f"{selected_unit}_风险评价_{datetime.date.today()}.txt",
                mime="text/plain"
            )
    else:
        st.info("点击上方按钮，利用大模型对当前经营单元的财务健康度进行全面诊断。")
