# -*- coding: utf-8 -*-
# normalize_v1_mt.py
import os
import subprocess
import glob
import re
import shutil
import sys
import time 
import threading 
from concurrent.futures import ThreadPoolExecutor, as_completed 

# --- 配置 ---
CONFIG = {
    "ffmpeg_path": "ffmpeg",
    "temp_prefix": "___temp_v1_thread_",
    # 使用 os.cpu_count() 获取逻辑处理器数量
    "max_workers": os.cpu_count() or 4 
}

# --- 辅助函数：运行子进程 ---
def run_command(cmd_list, timeout=60):
    try:
        result = subprocess.run(
            cmd_list, capture_output=True, text=True, encoding='utf-8',
            errors='ignore', check=False, timeout=timeout
        )
        return result
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd_list, -1, stderr=f"错误：找不到命令 '{cmd_list[0]}'")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd_list, -1, stderr=f"错误：命令执行超时 ({timeout}秒)")
    except Exception as e:
        return subprocess.CompletedProcess(cmd_list, -1, stderr=f"错误：执行命令时发生未知错误: {e}")

# --- 辅助函数：安全删除文件 ---
def safe_remove(filepath, lock, filename_for_log):
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except OSError as e:
        with lock: 
            print(f"  [{os.path.basename(filename_for_log)}] 警告：无法删除临时文件 '{os.path.basename(filepath)}'。错误: {e}")

# --- 单个文件处理函数 (核心逻辑) ---
def process_file(filename, target_peak_db, config, print_lock):
    """处理单个 WAV 文件 (版本 01 逻辑)。"""
    ffmpeg_path = config["ffmpeg_path"]
    temp_prefix = config["temp_prefix"]
    thread_id = threading.get_ident() 
    temp_wav_filename = f"{temp_prefix}{thread_id}_{os.path.basename(filename)}"
    
    def log_message(message):
        with print_lock:
            print(f"  [{os.path.basename(filename)}] {message}")

    log_message("开始处理 (v1)...")

    # 1. 检测峰值
    volumedetect_cmd = [
        ffmpeg_path, "-i", filename, "-af", "volumedetect",
        "-f", "null", "-nostats", "-"
    ]
    process_detect = run_command(volumedetect_cmd, timeout=60)
    if process_detect.returncode != 0 or "max_volume" not in process_detect.stderr.lower():
        log_message(f"错误：执行 volumedetect 失败或未找到音量信息。")
        log_message(f"  FFmpeg输出 (stderr): {process_detect.stderr[:500]}...")
        safe_remove(temp_wav_filename, print_lock, filename)
        return {'status': 'error', 'filename': filename, 'message': '无法检测音量'}

    # 2. 解析峰值
    current_peak_db = None
    match = re.search(r"max_volume:\s*([-\d\.]+)\s*dB", process_detect.stderr, re.IGNORECASE)
    if match:
        try:
            current_peak_db = float(match.group(1))
            log_message(f"检测到当前峰值: {current_peak_db:.2f} dB")
        except ValueError:
            log_message(f"错误：无法从 '{match.group(1)}' 解析峰值数值。")
            safe_remove(temp_wav_filename, print_lock, filename)
            return {'status': 'error', 'filename': filename, 'message': '无法解析峰值'}
    else:
        log_message("错误：未能在 ffmpeg 输出中找到 'max_volume'。")
        safe_remove(temp_wav_filename, print_lock, filename)
        return {'status': 'error', 'filename': filename, 'message': '未找到max_volume'}

    # 3. 计算增益
    gain_db = target_peak_db - current_peak_db
    gain_db_rounded = round(gain_db, 2)
    gain_str = f"{gain_db_rounded:.2f}" 
    log_message(f"计算得到需要调整的增益: {gain_str} dB.")

    # 4. 应用增益
    if gain_str == "0.00":
        log_message("增益为 0.00 dB，跳过处理。")
        safe_remove(temp_wav_filename, print_lock, filename)
        return {'status': 'skipped', 'filename': filename, 'message': '增益为0'}
    
    apply_gain_cmd = [
        ffmpeg_path, "-y", "-i", filename,
        "-af", f"volume={gain_str}dB",
        temp_wav_filename 
    ]
    log_message(f"正在应用增益，输出到临时文件 '{os.path.basename(temp_wav_filename)}' ...")
    process_apply = run_command(apply_gain_cmd, timeout=300)
    time.sleep(0.1) 

    if process_apply.returncode == 0 and os.path.exists(temp_wav_filename):
        log_message("处理成功，正在替换原始文件...")
        try:
            shutil.move(temp_wav_filename, filename) 
            log_message("文件已成功更新。")
            return {'status': 'processed', 'filename': filename, 'message': '处理成功'}
        except OSError as move_err:
            log_message(f"错误：无法替换文件。错误信息: {move_err}")
            log_message(f"临时文件 '{os.path.basename(temp_wav_filename)}' 可能已保留，请手动处理。")
            return {'status': 'error', 'filename': filename, 'message': f'替换文件失败: {move_err}'}
    else:
        log_message(f"错误：ffmpeg 应用增益时失败 (返回码 {process_apply.returncode})。")
        log_message(f"  FFmpeg输出 (stderr): {process_apply.stderr[:500]}...")
        safe_remove(temp_wav_filename, print_lock, filename) 
        return {'status': 'error', 'filename': filename, 'message': 'ffmpeg应用增益失败'}

# --- 主函数 (框架) ---
def main():
    """主处理逻辑，使用线程池并行处理文件"""
    print("=============================================")
    print(" 音频归一化脚本 v1 (基本归一化) - 多线程版")
    print("=============================================")
    print()
    
    target_peak_str = input(f"请输入目标峰值 (例如 -1): ")
    try:
        target_peak_db = float(target_peak_str)
    except ValueError:
        print(f"错误：输入 '{target_peak_str}' 不是有效的数字。程序将退出。")
        input("按 Enter 键退出...") 
        return 

    max_workers = CONFIG["max_workers"]
    print(f"将使用 {max_workers} 个线程并行处理 (根据系统逻辑处理器数量自动设定)。")
    print("-" * 20) 

    all_files = glob.glob("*.wav")
    wav_files = [f for f in all_files if not os.path.basename(f).startswith(CONFIG["temp_prefix"])]
    if not wav_files:
        print("当前目录下没有找到需要处理的 .wav 文件。")
        input("按 Enter 键退出...") 
        return 
    print(f"找到 {len(wav_files)} 个 .wav 文件准备处理...")

    processed_count = 0
    skipped_count = 0
    error_count = 0
    print_lock = threading.Lock() 
    futures = [] 
    
    try:
        # 清理残留
        for f in glob.glob(CONFIG["temp_prefix"] + "*.wav"):
            safe_remove(f, print_lock, "启动清理")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for filename in wav_files:
                # 提交任务
                future = executor.submit(process_file, filename, target_peak_db, CONFIG, print_lock)
                futures.append(future)
            
            print(f"已提交 {len(futures)} 个任务到线程池，开始处理...")

            # 获取结果
            for future in as_completed(futures):
                try:
                    result = future.result() 
                    if result['status'] == 'processed':
                        processed_count += 1
                    elif result['status'] == 'skipped':
                        skipped_count += 1
                    elif result['status'] == 'error':
                        error_count += 1
                except Exception as exc:
                    error_count += 1
                    with print_lock:
                         print(f"处理某个文件时发生未捕获的异常: {exc}") 

    finally:
        # 最终清理
        print("-" * 20)
        print("正在进行最终清理...")
        final_cleaned_count = 0
        for temp_file in glob.glob(CONFIG["temp_prefix"] + "*.wav"):
            safe_remove(temp_file, print_lock, "最终清理")
            final_cleaned_count += 1
        if final_cleaned_count > 0:
             print(f"最终清理完成，删除了 {final_cleaned_count} 个残留临时文件。")
        else:
             print("最终清理完成，未发现残留临时文件。")

    # 打印总结
    print("-" * 20)
    print("所有文件处理完毕！")
    print(f"结果统计：成功处理 {processed_count} 个文件，跳过 {skipped_count} 个文件，发生错误 {error_count} 个文件。")

# --- 程序入口 ---
if __name__ == "__main__":
    main()
    input("按 Enter 键退出...") 