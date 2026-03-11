import pandas as pd
import numpy as np
import os
import argparse

def process_group_aggregation(input_file, output_file):
    """
    根据输入文件，按照经营单元（县分处理2）和集团名（GROUP_NAME）进行数据汇总。
    输入格式参考 data_processing.py 的逻辑。
    """
    print(f"正在读取文件: {input_file} ...")
    try:
        # 读取 14万行数据，通常在 '结果集' 工作表
        df = pd.read_excel(input_file, sheet_name='结果集')
    except Exception as e:
        print(f"读取失败: {e}")
        return

    # 1. 预处理：重命名与缺失值处理
    if 'STAT_CYCLE' in df.columns:
        df['STAT_CYCLE'] = df['STAT_CYCLE'].astype(str)
    else:
        raise KeyError("输入文件缺失列：STAT_CYCLE")
    
    df['县分处理2'] = df['县分处理2'].fillna('未知责任单元')
    df['GROUP_NAME'] = df['GROUP_NAME'].fillna('非集团客户')

    print("正在进行维度转换与账龄计算...")

    # 2. 计算账龄区间辅助列 (参考 data_processing.py 逻辑)
    # 1个月内 = 上月欠费
    df['1个月内'] = df['上月欠费'].fillna(0)
    # 2-3个月 = 前1欠费 + 前2欠费
    df['2-3个月'] = df['前1欠费'].fillna(0) + df['前2欠费'].fillna(0)
    # 3-6个月 = 前3欠费 + 前4欠费 + 前5欠费
    df['3-6个月'] = df['前3欠费'].fillna(0) + df['前4欠费'].fillna(0) + df['前5欠费'].fillna(0)
    # 7-12个月 = 欠费7T12M
    df['7-12个月'] = df['欠费7T12M'].fillna(0)
    # 1年以上 = 欠费UP1Y
    df['1年以上'] = df['欠费UP1Y'].fillna(0)

    # 3. 聚合汇总
    print("正在按照【经营单元】和【集团名】进行汇总...")
    # 分组键：账期 + 经营单元 + 集团名
    group_keys = ['STAT_CYCLE', '县分处理2', 'GROUP_NAME']
    
    agg_dict = {
        '月初欠费': 'sum',
        '累计坏账': 'sum',
        '1个月内': 'sum',
        '2-3个月': 'sum',
        '3-6个月': 'sum',
        '7-12个月': 'sum',
        '1年以上': 'sum'
    }
    
    res_df = df.groupby(group_keys).agg(agg_dict).reset_index()
    
    # 重命名列
    res_df.rename(columns={
        'STAT_CYCLE': '账期',
        '县分处理2': '经营单元',
        'GROUP_NAME': '集团名'
    }, inplace=True)

    # 4. 信用减值计算 (跨账期逻辑)
    print("正在计算信用减值 (跨账期逻辑)...")
    
    def get_last_dec(cycle_str):
        try:
            year = int(cycle_str[:4])
            return f"{year-1}12"
        except:
            return None

    res_df['prev_dec_cycle'] = res_df['账期'].apply(get_last_dec)

    # 如果有历史输出文件，读取它以构建查找表
    bad_debt_lookup = {}
    if os.path.exists(output_file):
        try:
            existing_df = pd.read_excel(output_file)
            existing_df['账期'] = existing_df['账期'].astype(str)
            # 构建查找表：(经营单元, 集团名, 账期) -> 累计坏账
            bad_debt_lookup = existing_df.set_index(['经营单元', '集团名', '账期'])['累计坏账'].to_dict()
        except Exception as e:
            print(f"读取历史文件构建查找表失败: {e}")

    # 计算信用减值：当前累计坏账 - 上年12月该集团的累计坏账
    def calc_impairment(row):
        current_bad_debt = row['累计坏账']
        prev_dec = row['prev_dec_cycle']
        unit = row['经营单元']
        group = row['集团名']
        # 查找上一年12月的累计坏账
        prev_val = bad_debt_lookup.get((unit, group, prev_dec), 0)
        return current_bad_debt - prev_val

    res_df['信用减值'] = res_df.apply(calc_impairment, axis=1)

    # 5. 整理最终列顺序并保存
    final_cols = [
        '账期', '经营单元', '集团名', '月初欠费', '累计坏账', '信用减值',
        '1个月内', '2-3个月', '3-6个月', '7-12个月', '1年以上'
    ]
    
    final_res = res_df[final_cols]
    
    print(f"处理完成，正在保存至: {output_file}")
    
    # 如果已存在，则追加 (concat)
    if os.path.exists(output_file):
        try:
            old_df = pd.read_excel(output_file)
            # 避免账期类型不一致导致重复
            old_df['账期'] = old_df['账期'].astype(str)
            # 过滤掉已存在的相同账期数据（防止重复跑同一账期产生冗余）
            new_cycles = final_res['账期'].unique()
            old_df = old_df[~old_df['账期'].isin(new_cycles)]
            final_output = pd.concat([old_df, final_res], ignore_index=True)
        except Exception as e:
            print(f"合并旧数据失败，将直接覆盖: {e}")
            final_output = final_res
    else:
        final_output = final_res

    try:
        final_output.to_excel(output_file, index=False)
        print("保存成功！")
    except PermissionError:
        alt_output = f"集团汇总_另存_{final_res['账期'].iloc[0]}.xlsx"
        final_output.to_excel(alt_output, index=False)
        print(f"文件被占用，已另存为: {alt_output}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="集团维度欠费汇总处理脚本")
    parser.add_argument("input_file", help="输入 Excel 文件路径")
    parser.add_argument("output_file", help="输出 Excel 文件路径")
    args = parser.parse_args()

    if os.path.exists(args.input_file):
        process_group_aggregation(args.input_file, args.output_file)
    else:
        print(f"未找到输入文件: {args.input_file}")
