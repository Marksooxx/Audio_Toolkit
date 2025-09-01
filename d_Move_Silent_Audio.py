#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import subprocess
import re
from pathlib import Path
import shutil


def detect_volume(file_path):
    """
    调用 ffmpeg 检测文件的平均音量，返回字符串形式的音量值（例如 "-69.0" 或 "-inf"），若未检测到返回 None
    """
    cmd = ['ffmpeg', '-hide_banner', '-i', file_path, '-af', 'volumedetect', '-f', 'null', 'NUL']
    try:
        # 指定编码为 utf-8，错误采用替换方式，防止 cp932 解码错误
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8', errors='replace')
        output = result.stderr
    except Exception as e:
        print(f"执行 ffmpeg 出错: {e}")
        return None

    # 匹配 "mean_volume: -69.0 dB" 或 "mean_volume: -inf dB"
    match = re.search(r"mean_volume:\s*([-\d\.inf]+)\s*dB", output, re.IGNORECASE)
    if match:
        vol_str = match.group(1).strip()
        return vol_str
    return None


def main():
    # 设置静音判断阈值（默认为 -60 dB，可根据需要调整）
    threshold = -80.0
    silent_folder = Path("SilentAudio")
    silent_folder.mkdir(exist_ok=True)

    # 获取当前目录下的音频文件列表
    extensions = ('.wav', '.mp3', '.flac')
    current_dir = Path('.')
    files = [f for f in current_dir.iterdir() if f.is_file() and f.suffix.lower() in extensions]
    if not files:
        print("当前目录没有音频文件.")
        return

    for file_path in files:
        print(f"\n正在处理 {file_path.name} ...")
        vol = detect_volume(str(file_path))
        move_decision = False

        if vol is None:
            print(f"未检测到音量信息, 将 {file_path.name} 视为无声.")
            move_decision = True
        elif vol.lower() == "-inf":
            print(f"检测到无声音文件, {file_path.name} 的平均音量为: {vol}")
            move_decision = True
        else:
            try:
                vol_value = float(vol)
                print(f"{file_path.name} 的平均音量为: {vol_value} dB")
                if vol_value <= threshold:
                    print(f"低于阈值 {threshold} dB, 将 {file_path.name} 视为无声.")
                    move_decision = True
                else:
                    print(f"{file_path.name} 检测为有声音.")
            except ValueError:
                print(f"无法解析音量数值: {vol}")

        if move_decision:
            dest_path = silent_folder / file_path.name
            try:
                shutil.move(str(file_path), dest_path)
                print(f"移动成功: {file_path.name} -> {dest_path}")
            except Exception as e:
                print(f"移动失败: {file_path.name}\n错误: {e}")

    input("\n处理完成，按回车键退出...")


if __name__ == "__main__":
    main()
