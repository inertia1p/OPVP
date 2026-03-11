import pandas as pd
import numpy as np
import os
import argparse

def process_overdue_data(input_file, output_file):
    """
    读取大容量 Excel 文件，按要求分组汇聚求和。
    14万行数据建议使用 pandas + openpyxl 引擎。
    """
    print(f"正在读取文件: {input_file} ...")
    # 14万行建议分块读取或确保内存充足，这里直接读取
    try:
        df = pd.read_excel(input_file, sheet_name='结果集')
    except Exception as e:
        print(f"读取失败: {e}")
        return
    
    # 预处理：确保账期和责任单元是字符串，方便分组
    if 'STAT_CYCLE' in df.columns:
        df['STAT_CYCLE'] = df['STAT_CYCLE'].astype(str)
    else:
        raise KeyError("输入文件缺失列：STAT_CYCLE，请检查列名拼写或文件格式")
    df['县分处理2'] = df['县分处理2'].fillna('未知责任单元')

    # 如果读取的excel中没有回款，默认为0
    if '回款' not in df.columns:
        df['回款'] = 0

    print("正在进行基础数据聚合...")

    # 定义分组键：账期 + 责任单元
    group_keys = ['STAT_CYCLE', '县分处理2']

    # 1. 基础求和逻辑
    # 手机/宽带/专线等逻辑涉及到条件求和，我们先定义辅助列或使用 apply
    
    # 手机、宽带
    df['手机_月初欠费'] = np.where(df['网别'] == '手机', df['月初欠费'], 0)
    df['宽带_月初欠费'] = np.where(df['网别'] == '宽带', df['月初欠费'], 0)

    # 专线: BRAND_ID 在 [JRJP, WYCZ, DBA4, HLPT, MSTP, YUNL]
    special_brands = ['JRJP', 'WYCZ', 'DBA4', 'HLPT', 'MSTP', 'YUNL']
    df['专线_月初欠费'] = np.where(df['BRAND_ID'].isin(special_brands), df['月初欠费'], 0)

    # 连接: BRAND_ID 为 WULW
    df['连接_月初欠费'] = np.where(df['BRAND_ID'] == 'WULW', df['月初欠费'], 0)

    # IDC: 业务分类 为 IDC
    df['IDC_月初欠费'] = np.where(df['业务分类'] == 'IDC', df['月初欠费'], 0)

    # 标品（不含连接）：业务分类在 [云计算，大数据，物联网]但BRAND_ID不为WULW
    standard_products = ['云计算', '大数据', '物联网']
    df['标品_不含连接_月初欠费'] = np.where(
        (df['业务分类'].isin(standard_products)) & (df['BRAND_ID'] != 'WULW'),
        df['月初欠费'],
        0
    )

    # 开始聚合
    agg_dict = {
        '月初欠费': 'sum',
        '累计坏账': 'sum',
        '上月欠费': 'sum',
        '前1欠费': 'sum',
        '前2欠费': 'sum',
        '前3欠费': 'sum',
        '前4欠费': 'sum',
        '前5欠费': 'sum',
        '欠费7T12M': 'sum',
        '欠费UP1Y': 'sum',
        '手机_月初欠费': 'sum',
        '宽带_月初欠费': 'sum',
        '专线_月初欠费': 'sum',
        '连接_月初欠费': 'sum',
        'IDC_月初欠费': 'sum',
        '标品_不含连接_月初欠费': 'sum',
        '回款': 'sum'
    }

    res_df = df.groupby(group_keys).agg(agg_dict).reset_index()

    # 重命名列以匹配输出要求
    res_df.rename(columns={
        'STAT_CYCLE': '账期',
        '县分处理2': '责任单元',
        '上月欠费': '1个月内',
        '手机_月初欠费': '手机',
        '宽带_月初欠费': '宽带',
        '专线_月初欠费': '专线',
        '连接_月初欠费': '连接',
        'IDC_月初欠费': 'IDC',
        '欠费7T12M': '7-12个月',
        '欠费UP1Y': '1年以上'
    }, inplace=True)

    # 计算 2-3个月 (前1 + 前2)
    res_df['2-3个月'] = res_df['前1欠费'] + res_df['前2欠费']

    # 计算 3-6个月 (前3 + 前4 + 前5)
    res_df['3-6个月'] = res_df['前3欠费'] + res_df['前4欠费'] + res_df['前5欠费']

    # 计算 标品（不含连接）= 标品含连接 - 连接
    res_df['标品（不含连接）'] = res_df['标品_不含连接_月初欠费']

    # 5. 信用减值计算：当账期的累计坏账 - 上一年12月账期的累计坏账
    print("正在计算信用减值 (跨账期逻辑)...")
    
    # 构造上一年12月的账期字符串，例如 "202403" -> "202312"
    def get_last_dec(cycle_str):
        try:
            year = int(cycle_str[:4])
            return f"{year-1}12"
        except:
            return None

    res_df['prev_dec_cycle'] = res_df['账期'].apply(get_last_dec)


    # 一次性读取输出文件（若存在），同时用于构建查找表与后续追加
    existing_df = None
    if os.path.exists(output_file):
        try:
            existing_df = pd.read_excel(output_file)
            existing_df['账期'] = existing_df['账期'].astype(str)
            # 构建查找表：责任单元 + 账期 -> 累计坏账
            bad_debt_lookup = existing_df.set_index(['责任单元', '账期'])['累计坏账'].to_dict()
        except Exception as e:
            print(f"读取历史输出文件失败，将使用空查找表: {e}")
            bad_debt_lookup = {}
    else:
        bad_debt_lookup = {}

    def calc_impairment(row):
        current_bad_debt = row['累计坏账']
        prev_dec = row['prev_dec_cycle']
        unit = row['责任单元']
        # 查找上一年12月的累计坏账
        prev_val = bad_debt_lookup.get((unit, prev_dec), 0)  # 如果没找到则默认为0
        return current_bad_debt - prev_val

    res_df['信用减值'] = res_df.apply(calc_impairment, axis=1)

    # 整理最终列顺序
    final_cols = [
        '账期', '责任单元', '月初欠费', '累计坏账', '信用减值',
        '1个月内', '2-3个月', '3-6个月', '7-12个月', '1年以上',
        '手机', '宽带', '专线', '连接', 'IDC', '标品（不含连接）', '回款'
    ]

    # 确保所有要求的列都存在（防止某些输入列缺失导致报错）
    final_res = res_df[final_cols]

    print(f"处理完成，正在追加保存至: {output_file}")
    # 复用已读取的 existing_df，避免再次读取
    if existing_df is not None:
        combined_df = pd.concat([existing_df, final_res], ignore_index=True)
    else:
        combined_df = final_res

    # 获取当前账期，用于生成备用文件名
    current_cycle = final_res['账期'].iloc[0] if not final_res.empty else 'unknown'

    try:
        combined_df.to_excel(output_file, index=False)
        print("保存成功！")
    except PermissionError:
        # 如果文件被占用，生成新文件名并保存
        alt_output = f"{os.path.splitext(output_file)[0]}_追加_{current_cycle}.xlsx"
        print(f"目标文件被占用，正在另存为: {alt_output}")
        combined_df.to_excel(alt_output, index=False)
        print("另存成功！")
    return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="政企线欠费汇总处理脚本")
    #'K:\Overdue Payment Visualization Platform\OPVP\历年汇总\2025-2026汇总.xlsx
    parser.add_argument("input_file", help="输入 Excel 文件路径")
    parser.add_argument("output_file", help="输出 Excel 文件路径")
    args = parser.parse_args()

    if os.path.exists(args.input_file):
        process_overdue_data(args.input_file, args.output_file)
    else:
        print(f"未找到输入文件 {args.input_file}，请确保文件存在。")
