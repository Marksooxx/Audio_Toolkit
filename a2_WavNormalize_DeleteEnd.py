# -*- coding: utf-8 -*-
# normalize_v2_mt.py
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
    "temp_prefix": "___temp_v2_thread_",
    "norm_suffix": "_norm", # 归一化临时文件后缀
    "silence_suffix": "_silence", # 静音删除临时文件后缀
    "max_workers": os.cpu_count() or 4,
    # 静音删除参数
    "silence_stop_duration": "1", 
    "silence_stop_threshold": "-50dB"
}

# --- 辅助函数：运行子进程 (同v1) ---
def run_command(cmd_list, timeout=60):
    # ... (代码同 v1) ...
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


# --- 辅助函数：安全删除文件 (同v1) ---
def safe_remove(filepath, lock, filename_for_log):
    # ... (代码同 v1) ...
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except OSError as e:
        with lock: 
            print(f"  [{os.path.basename(filename_for_log)}] 警告：无法删除临时文件 '{os.path.basename(filepath)}'。错误: {e}")


# --- 单个文件处理函数 (核心逻辑) ---
def process_file(filename, target_peak_db, config, print_lock):
    """处理单个 WAV 文件 (版本 02 逻辑)。"""
    ffmpeg_path = config["ffmpeg_path"]
    temp_prefix = config["temp_prefix"]
    norm_suffix = config["norm_suffix"]
    silence_suffix = config["silence_suffix"]
    thread_id = threading.get_ident() 
    
    # 构造临时文件名
    base_temp_name = f"{temp_prefix}{thread_id}_{os.path.basename(filename)}"
    temp_norm_filename = base_temp_name + norm_suffix + ".wav"
    temp_silence_filename = base_temp_name + silence_suffix + ".wav"
    
    all_temps = [temp_norm_filename, temp_silence_filename] # 用于清理

    def log_message(message):
        with print_lock:
            print(f"  [{os.path.basename(filename)}] {message}")
            
    def cleanup_temps():
        for f in all_temps:
            safe_remove(f, print_lock, filename)

    log_message("开始处理 (v2 - 归一化+静音删除)...")

    # --- 阶段 1: 归一化处理 ---
    log_message("阶段 1: 检查归一化...")
    
    # 1. 检测峰值
    volumedetect_cmd = [ffmpeg_path, "-i", filename, "-af", "volumedetect", "-f", "null", "-nostats", "-"]
    process_detect = run_command(volumedetect_cmd, timeout=60)
    if process_detect.returncode != 0 or "max_volume" not in process_detect.stderr.lower():
        log_message(f"错误：无法检测音量。跳过后续处理。")
        cleanup_temps()
        return {'status': 'error', 'filename': filename, 'message': '无法检测音量'}

    # 2. 解析峰值
    current_peak_db = None
    match = re.search(r"max_volume:\s*([-\d\.]+)\s*dB", process_detect.stderr, re.IGNORECASE)
    if match:
        try:
            current_peak_db = float(match.group(1))
            log_message(f"  检测到峰值: {current_peak_db:.2f} dB")
        except ValueError:
            log_message(f"  错误：无法解析峰值。跳过后续处理。")
            cleanup_temps()
            return {'status': 'error', 'filename': filename, 'message': '无法解析峰值'}
    else:
        log_message("  错误：未找到max_volume。跳过后续处理。")
        cleanup_temps()
        return {'status': 'error', 'filename': filename, 'message': '未找到max_volume'}

    # 3. 计算增益
    gain_db = target_peak_db - current_peak_db
    gain_db_rounded = round(gain_db, 2)
    gain_str = f"{gain_db_rounded:.2f}" 
    log_message(f"  计算得到增益: {gain_str} dB.")

    # 4. 应用增益 (如果需要)
    input_for_silence_removal = filename # 默认使用原文件进行静音删除
    normalization_applied = False
    
    if gain_str == "0.00":
        log_message("  增益为 0.00 dB，跳过归一化步骤。")
    else:
        apply_gain_cmd = [ffmpeg_path, "-y", "-i", filename, "-af", f"volume={gain_str}dB", temp_norm_filename]
        log_message(f"  正在应用增益到 '{os.path.basename(temp_norm_filename)}'...")
        process_apply = run_command(apply_gain_cmd, timeout=300)
        time.sleep(0.1)

        if process_apply.returncode == 0 and os.path.exists(temp_norm_filename):
            log_message("  归一化成功。")
            input_for_silence_removal = temp_norm_filename # 后续静音处理使用这个文件
            normalization_applied = True
        else:
            log_message(f"  错误：应用增益失败 (返回码 {process_apply.returncode})。跳过后续静音删除。")
            log_message(f"    FFmpeg输出: {process_apply.stderr[:500]}...")
            cleanup_temps()
            return {'status': 'error', 'filename': filename, 'message': 'ffmpeg应用增益失败'}

    # --- 阶段 2: 尾部静音删除 ---
    log_message("阶段 2: 处理尾部静音删除...")
    silence_cmd = [
        ffmpeg_path, "-y",
        "-i", input_for_silence_removal, # 输入可能是原文件或归一化后的临时文件
        "-af", f"silenceremove=start_periods=0:stop_periods=-1:stop_duration={config['silence_stop_duration']}:stop_threshold={config['silence_stop_threshold']}",
        temp_silence_filename
    ]
    log_message(f"  执行静音删除到 '{os.path.basename(temp_silence_filename)}'...")
    process_silence = run_command(silence_cmd, timeout=300)
    time.sleep(0.1)

    if process_silence.returncode == 0 and os.path.exists(temp_silence_filename):
        log_message("  静音删除处理成功，正在替换原始文件...")
        try:
            shutil.move(temp_silence_filename, filename)
            log_message("  文件已成功更新。")
            # 清理可能存在的归一化临时文件
            if normalization_applied:
                 safe_remove(temp_norm_filename, print_lock, filename)
            
            status = 'processed' if normalization_applied else 'processed_silence_only' # 或用 skipped 更好?
            # 保持与 v1 一致，只要最终替换成功就算 processed
            return {'status': 'processed', 'filename': filename, 'message': '处理成功 (含静音删除)'} 
        except OSError as move_err:
            log_message(f"  错误：无法替换最终文件。错误: {move_err}")
            log_message(f"  临时文件 '{os.path.basename(temp_silence_filename)}' 可能已保留。")
            cleanup_temps() # 尝试清理所有临时文件
            return {'status': 'error', 'filename': filename, 'message': f'替换最终文件失败: {move_err}'}
    else:
        log_message(f"  错误：执行静音删除失败 (返回码 {process_silence.returncode})。")
        log_message(f"    FFmpeg输出: {process_silence.stderr[:500]}...")
        cleanup_temps() # 清理所有临时文件
        # 如果归一化成功了但静音删除失败，原始文件没有被修改
        # 如果归一化没做，静音删除失败，原始文件也没变
        # 都算作错误
        return {'status': 'error', 'filename': filename, 'message': '静音删除失败'}

# --- 主函数 (框架, 与v1基本相同, 只改标题和CONFIG) ---
def main():
    """主处理逻辑，使用线程池并行处理文件"""
    print("================================================")
    print(" 音频处理脚本 v2 (归一化+静音删除) - 多线程版")
    print("================================================")
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
    skipped_count = 0 # 这个版本跳过的概念不明确，统一用 processed/error
    error_count = 0
    print_lock = threading.Lock() 
    futures = [] 
    
    try:
        # 清理残留
        for f in glob.glob(CONFIG["temp_prefix"] + "*.wav"):
            safe_remove(f, print_lock, "启动清理")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for filename in wav_files:
                future = executor.submit(process_file, filename, target_peak_db, CONFIG, print_lock)
                futures.append(future)
            
            print(f"已提交 {len(futures)} 个任务到线程池，开始处理...")

            for future in as_completed(futures):
                try:
                    result = future.result() 
                    # 根据 v2 的 process_file 返回状态调整计数
                    if result['status'] == 'processed' or result['status'] == 'processed_silence_only':
                         processed_count += 1
                    elif result['status'] == 'skipped': # 虽然 v2 不太可能返回 skipped
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

    print("-" * 20)
    print("所有文件处理完毕！")
    # V2 的 'skipped' 意义不大，主要看 processed 和 error
    print(f"结果统计：成功处理 {processed_count} 个文件，发生错误 {error_count} 个文件。")

# --- 程序入口 ---
if __name__ == "__main__":
    main()
    input("按 Enter 键退出...") 