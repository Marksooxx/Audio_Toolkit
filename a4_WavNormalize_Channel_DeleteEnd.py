# -*- coding: utf-8 -*-
# normalize_v4_mt.py
import os
import subprocess
import glob
import re
import shutil
import sys
import time 
import threading 
import math
from concurrent.futures import ThreadPoolExecutor, as_completed 

# --- 配置 ---
CONFIG = {
    "ffmpeg_path": "ffmpeg",
    "ffprobe_path": "ffprobe",
    "temp_prefix": "___temp_v4_thread_",
    "norm_suffix": "_norm",     # 归一化临时输出
    "silence_suffix": "_silence", # 静音删除临时输出
    "left_suffix": "_left",     # 分离左声道
    "right_suffix": "_right",    # 分离右声道
    "max_workers": os.cpu_count() or 4,
    "gain_tolerance": 0.1,
    # 静音删除参数
    "silence_stop_duration": "1", 
    "silence_stop_threshold": "-50dB"
}

# --- 辅助函数：运行子进程 (同v3) ---
def run_command(cmd_list, timeout=60, suppress_output=False):
    # ... (代码同 v3) ...
    if not suppress_output:
         pass
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


# --- 辅助函数：安全删除文件 (同v3) ---
def safe_remove(filepath, lock, filename_for_log):
    # ... (代码同 v3) ...
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except OSError as e:
        with lock: 
            print(f"  [{os.path.basename(filename_for_log)}] 警告：无法删除临时文件 '{os.path.basename(filepath)}'。错误: {e}")


# --- 辅助函数：获取声道数 (同v3) ---
def get_audio_channels(filename, ffprobe_path, lock):
    # ... (代码同 v3) ...
    ffprobe_cmd = [ffprobe_path, "-v", "error", "-select_streams", "a:0", 
                   "-show_entries", "stream=channels", "-of", "default=noprint_wrappers=1:nokey=1", filename]
    result = run_command(ffprobe_cmd, timeout=30, suppress_output=True) 
    if result and result.returncode == 0 and result.stdout.strip():
        try:
            return int(result.stdout.strip())
        except ValueError:
             with lock: print(f"  [{os.path.basename(filename)}] 错误：无法从 ffprobe 输出 '{result.stdout.strip()}' 解析声道数。")
             return None
    else:
        return None

# --- 辅助函数：获取单声道峰值 (同v3) ---
def get_mono_peak(filename, ffmpeg_path, lock):
    # ... (代码同 v3) ...
    ffmpeg_cmd = [ffmpeg_path, "-i", filename, "-af", "volumedetect", "-f", "null", "-nostats", "-"]
    result = run_command(ffmpeg_cmd, timeout=60, suppress_output=True) 
    if result: 
        match = re.search(r"max_volume:\s*([-\d\.]+)\s*dB", result.stderr, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
    return None

# --- 辅助函数：获取立体声峰值 (同v3) ---
def get_stereo_peaks_via_split(filename, config, thread_id, lock):
    # ... (代码同 v3) ...
    ffmpeg_path = config["ffmpeg_path"]
    temp_prefix = config["temp_prefix"]
    left_suffix = config["left_suffix"]
    right_suffix = config["right_suffix"]
    
    base_temp_name = f"{temp_prefix}{thread_id}_{os.path.basename(filename)}"
    temp_left = base_temp_name + left_suffix + ".wav"
    temp_right = base_temp_name + right_suffix + ".wav"
    
    peak_l, peak_r = None, None
    cmd_left = [ffmpeg_path, "-y", "-i", filename, "-af", f"pan=mono|c0=1*c0", temp_left]
    result_left = run_command(cmd_left, timeout=120, suppress_output=True)
    cmd_right = [ffmpeg_path, "-y", "-i", filename, "-af", f"pan=mono|c0=1*c1", temp_right]
    result_right = run_command(cmd_right, timeout=120, suppress_output=True)
    time.sleep(0.1) 

    if result_left and result_left.returncode == 0 and os.path.exists(temp_left):
        peak_l = get_mono_peak(temp_left, ffmpeg_path, lock)
    if result_right and result_right.returncode == 0 and os.path.exists(temp_right):
        peak_r = get_mono_peak(temp_right, ffmpeg_path, lock)

    safe_remove(temp_left, lock, filename)
    safe_remove(temp_right, lock, filename)

    if peak_l is not None and peak_r is not None:
        return {'L': peak_l, 'R': peak_r}
    else:
        with lock: print(f"  [{os.path.basename(filename)}] 错误：未能获取立体声峰值。")
        return None


# --- 单个文件处理函数 (核心逻辑) ---
def process_file(filename, target_peak_db, config, print_lock):
    """处理单个 WAV 文件 (版本 04 逻辑)。"""
    ffmpeg_path = config["ffmpeg_path"]
    ffprobe_path = config["ffprobe_path"]
    temp_prefix = config["temp_prefix"]
    norm_suffix = config["norm_suffix"]
    silence_suffix = config["silence_suffix"]
    gain_tolerance = config["gain_tolerance"]
    thread_id = threading.get_ident() 
    
    base_temp_name = f"{temp_prefix}{thread_id}_{os.path.basename(filename)}"
    temp_norm_filename = base_temp_name + norm_suffix + ".wav"
    temp_silence_filename = base_temp_name + silence_suffix + ".wav"
    
    # 包含所有可能产生的临时文件
    all_temps = [temp_norm_filename, temp_silence_filename,
                 base_temp_name + config["left_suffix"] + ".wav", 
                 base_temp_name + config["right_suffix"] + ".wav"]

    def log_message(message):
        with print_lock:
            print(f"  [{os.path.basename(filename)}] {message}")
            
    def cleanup_temps():
        for f in all_temps:
            safe_remove(f, print_lock, filename)

    log_message("开始处理 (v4 - 分声道归一化 + 静音删除)...")
    
    proceed_to_silence_removal = True # 标记是否继续
    normalization_applied = False
    input_for_silence_removal = filename # 默认输入是原文件
    
    channels = get_audio_channels(filename, ffprobe_path, print_lock)
    if channels is None:
        log_message("错误：无法确定声道数。跳过所有处理。")
        cleanup_temps()
        return {'status': 'error', 'filename': filename, 'message': '无法获取声道数'}

    # --- 阶段 1: 归一化处理 ---
    log_message("阶段 1: 检查归一化...")
    apply_gain = False
    ffmpeg_normalize_cmd = None

    # === 单声道归一化判断 ===
    if channels == 1:
        log_message("  检测到单声道文件。正在检测峰值...")
        current_peak_db = get_mono_peak(filename, ffmpeg_path, print_lock)
        if current_peak_db is None:
            log_message("  错误：无法检测单声道峰值。跳过所有处理。")
            proceed_to_silence_removal = False
            cleanup_temps()
            return {'status': 'error', 'filename': filename, 'message': '无法检测单声道峰值'}
        
        log_message(f"    当前峰值: {current_peak_db:.2f} dB")
        gain_db = target_peak_db - current_peak_db
        gain_db_rounded = round(gain_db, 2)
        log_message(f"    计算得到增益: {gain_db_rounded:.2f} dB")
        
        if abs(gain_db_rounded) < gain_tolerance:
            log_message(f"    增益绝对值低于阈值 {gain_tolerance} dB。跳过归一化步骤。")
        else:
            apply_gain = True
            gain_str = f"{gain_db_rounded:.2f}"
            ffmpeg_normalize_cmd = [ffmpeg_path, "-y", "-i", filename, "-af", f"volume={gain_str}dB", temp_norm_filename]

    # === 立体声归一化判断 ===
    elif channels == 2:
        log_message("  检测到立体声文件。正在检测峰值 (分离声道)...")
        stereo_peaks = get_stereo_peaks_via_split(filename, config, thread_id, print_lock)
        if stereo_peaks is None:
            log_message("  错误：无法检测立体声峰值。跳过所有处理。")
            proceed_to_silence_removal = False
            cleanup_temps() # get_stereo_peaks_via_split 会清理自己的临时文件
            return {'status': 'error', 'filename': filename, 'message': '无法检测立体声峰值'}

        log_message(f"    检测到峰值: 左 {stereo_peaks['L']:.2f} dB, 右 {stereo_peaks['R']:.2f} dB")
        gain_l = target_peak_db - stereo_peaks['L']
        gain_r = target_peak_db - stereo_peaks['R']
        gain_l_rounded = round(gain_l, 2)
        gain_r_rounded = round(gain_r, 2)
        log_message(f"    计算得到增益: 左 {gain_l_rounded:.2f} dB, 右 {gain_r_rounded:.2f} dB")

        if abs(gain_l_rounded) < gain_tolerance and abs(gain_r_rounded) < gain_tolerance:
            log_message(f"    左右声道增益绝对值均低于阈值 {gain_tolerance} dB。跳过归一化步骤。")
        else:
            apply_gain = True
            gain_l_str = f"{gain_l_rounded:.2f}"
            gain_r_str = f"{gain_r_rounded:.2f}"
            filter_complex = (f"channelsplit=channel_layout=stereo[FL][FR];"
                              f"[FL]volume={gain_l_str}dB[left];"
                              f"[FR]volume={gain_r_str}dB[right];"
                              f"[left][right]amerge=inputs=2")
            ffmpeg_normalize_cmd = [ffmpeg_path, "-y", "-i", filename, "-filter_complex", filter_complex, temp_norm_filename]

    # === 不支持的声道数 ===
    else:
        log_message(f"文件声道数为 {channels}，不支持。跳过所有处理。")
        proceed_to_silence_removal = False
        cleanup_temps()
        return {'status': 'skipped', 'filename': filename, 'message': f'不支持的声道数: {channels}'}

    # --- 执行归一化 (如果需要) ---
    if apply_gain and ffmpeg_normalize_cmd and proceed_to_silence_removal:
        log_message("  需要归一化，正在应用增益...")
        result_norm = run_command(ffmpeg_normalize_cmd, timeout=300)
        time.sleep(0.1) 

        if result_norm and result_norm.returncode == 0 and os.path.exists(temp_norm_filename):
            log_message("    归一化成功。")
            input_for_silence_removal = temp_norm_filename # 更新静音处理的输入文件
            normalization_applied = True
        else:
            log_message(f"    错误：应用归一化增益失败 (返回码 {result_norm.returncode})。跳过后续静音删除。")
            log_message(f"      FFmpeg输出: {result_norm.stderr[:500]}...")
            proceed_to_silence_removal = False
            cleanup_temps() # 清理所有临时文件
            # 归一化失败，文件未被修改，算作错误
            return {'status': 'error', 'filename': filename, 'message': '归一化失败'}

    # --- 阶段 2: 执行尾部静音删除 ---
    if proceed_to_silence_removal:
        log_message("阶段 2: 处理尾部静音删除...")
        silence_cmd = [
            ffmpeg_path, "-y",
            "-i", input_for_silence_removal, 
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
                # 清理归一化临时文件（如果之前生成了）
                if normalization_applied:
                    safe_remove(temp_norm_filename, print_lock, filename)
                
                # 无论归一化是否执行，只要最终替换成功就算 processed
                status_msg = "处理成功 (归一化+静音删除)" if normalization_applied else "处理成功 (仅静音删除或无操作)"
                return {'status': 'processed', 'filename': filename, 'message': status_msg} 
            except OSError as move_err:
                log_message(f"  错误：无法替换最终文件。错误: {move_err}")
                log_message(f"  临时文件 '{os.path.basename(temp_silence_filename)}' 可能已保留。")
                cleanup_temps()
                return {'status': 'error', 'filename': filename, 'message': f'替换最终文件失败: {move_err}'}
        else:
            log_message(f"  错误：执行静音删除失败 (返回码 {process_silence.returncode})。")
            log_message(f"    FFmpeg输出: {process_silence.stderr[:500]}...")
            cleanup_temps()
            # 如果归一化成功但静音删除失败，文件停留在归一化状态，这不符合预期，算错误
            # 如果归一化跳过但静音删除失败，文件未变，也算错误
            return {'status': 'error', 'filename': filename, 'message': '静音删除失败'}
    else:
         # 如果因为之前的错误跳过了静音删除
         log_message("因先前步骤失败或不支持，跳过静音删除。")
         # 状态已经在前面返回了，这里不需要额外操作
         pass
         # 安全起见，返回一个错误状态以防万一流程走到这里
         cleanup_temps()
         return {'status': 'error', 'filename': filename, 'message': '处理流程异常中断'}

# --- 主函数 (框架, 与v3基本相同, 只改标题和CONFIG) ---
def main():
    """主处理逻辑，使用线程池并行处理文件"""
    print("======================================================")
    print(" 音频处理脚本 v4 (分声道归一化+静音删除) - 多线程版")
    print("======================================================")
    print()
    
    target_peak_str = input(f"请输入目标峰值 (例如 -4.5): ") 
    try:
        target_peak_db = float(target_peak_str)
    except ValueError:
        print(f"错误：输入 '{target_peak_str}' 不是有效的数字。程序将退出。")
        input("按 Enter 键退出...") 
        return 

    if not shutil.which(CONFIG["ffprobe_path"]):
         print(f"错误：找不到 ffprobe 命令 ('{CONFIG['ffprobe_path']}')。此脚本需要 ffprobe。")
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
    skipped_count = 0 # v4 跳过的概念更复杂，主要是指因阈值跳过归一化
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

    print("-" * 20)
    print("所有文件处理完毕！")
    # V4 的 skipped 指的是因阈值跳过归一化(或声道不支持)
    print(f"结果统计：成功处理 {processed_count} 个文件，跳过 {skipped_count} 个文件，发生错误 {error_count} 个文件。")
    print("(注：'成功处理' 指整个流程完成，无论是否执行了归一化)")


# --- 程序入口 ---
if __name__ == "__main__":
    main()
    input("按 Enter 键退出...") 