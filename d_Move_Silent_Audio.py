#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import subprocess
import re
import win32api
import win32con


def add_long_path_prefix(path):
    """
    添加 \\?\ 前缀以支持长路径和 Unicode 文件名
    """
    if path.startswith('\\\\?\\'):
        return path
    return '\\\\?\\' + os.path.abspath(path)


def move_file(src, dest):
    """
    使用 Windows API 移动文件（替换目标文件）
    """
    src_long = add_long_path_prefix(src)
    dest_long = add_long_path_prefix(dest)
    try:
        win32api.MoveFileEx(src_long, dest_long, win32con.MOVEFILE_REPLACE_EXISTING)
        print(f"移动成功: {src} -> {dest}")
    except Exception as e:
        print(f"移动失败: {src}\n错误: {e}")


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
    threshold = -60.0
    silent_folder = "SilentAudio"
    if not os.path.exists(silent_folder):
        os.makedirs(silent_folder)

    # 获取当前目录下的音频文件列表
    extensions = ('.wav', '.mp3', '.flac')
    files = [f for f in os.listdir('.') if f.lower().endswith(extensions) and os.path.isfile(f)]
    if not files:
        print("当前目录没有音频文件.")
        return

    for file in files:
        print(f"\n正在处理 {file} ...")
        vol = detect_volume(file)
        move_decision = False

        if vol is None:
            print(f"未检测到音量信息, 将 {file} 视为无声.")
            move_decision = True
        elif vol.lower() == "-inf":
            print(f"检测到无声音文件, {file} 的平均音量为: {vol}")
            move_decision = True
        else:
            try:
                vol_value = float(vol)
                print(f"{file} 的平均音量为: {vol_value} dB")
                if vol_value <= threshold:
                    print(f"低于阈值 {threshold} dB, 将 {file} 视为无声.")
                    move_decision = True
                else:
                    print(f"{file} 检测为有声音.")
            except ValueError:
                print(f"无法解析音量数值: {vol}")

        if move_decision:
            src_path = os.path.abspath(file)
            dest_path = os.path.join(os.path.abspath(silent_folder), file)
            move_file(src_path, dest_path)

    input("\n处理完成，按回车键退出...")


if __name__ == "__main__":
    main()
