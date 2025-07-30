# -*- coding: utf-8 -*-
# normalize_v3_mt.py
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
    "ffprobe_path": "ffprobe", # 需要 ffprobe
    "temp_prefix": "___temp_v3_thread_",
    "output_suffix": "_output", # 主输出临时文件
    "left_suffix": "_left",     # 分离左声道临时文件
    "right_suffix": "_right",    # 分离右声道临时文件
    "max_workers": os.cpu_count() or 4,
    "gain_tolerance": 0.1 # 增益容忍阈值
}

# --- 辅助函数：运行子进程 (同v1) ---
def run_command(cmd_list, timeout=60, suppress_output=False):
    # ... (代码同 v1, 但加回 suppress_output 参数) ...
    if not suppress_output:
         # print(f"  执行: {description}...") # 保持日志简洁，除非调试
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

# --- 辅助函数：安全删除文件 (同v1) ---
def safe_remove(filepath, lock, filename_for_log):
    # ... (代码同 v1) ...
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except OSError as e:
        with lock: 
            print(f"  [{os.path.basename(filename_for_log)}] 警告：无法删除临时文件 '{os.path.basename(filepath)}'。错误: {e}")

# --- 辅助函数：获取声道数 ---
def get_audio_channels(filename, ffprobe_path, lock):
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
        # with lock: print(f"  [{os.path.basename(filename)}] 警告：ffprobe 获取声道数失败。")
        return None

# --- 辅助函数：获取单声道峰值 ---
def get_mono_peak(filename, ffmpeg_path, lock):
    ffmpeg_cmd = [ffmpeg_path, "-i", filename, "-af", "volumedetect", "-f", "null", "-nostats", "-"]
    result = run_command(ffmpeg_cmd, timeout=60, suppress_output=True) 
    if result: 
        match = re.search(r"max_volume:\s*([-\d\.]+)\s*dB", result.stderr, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                # with lock: print(f"  [{os.path.basename(filename)}] 错误：无法解析单声道峰值。")
                return None
        # else: with lock: print(f"  [{os.path.basename(filename)}] 警告：未找到单声道 max_volume。")
    return None

# --- 辅助函数：获取立体声峰值 (分离文件法) ---
def get_stereo_peaks_via_split(filename, config, thread_id, lock):
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
    """处理单个 WAV 文件 (版本 03 逻辑)。"""
    ffmpeg_path = config["ffmpeg_path"]
    ffprobe_path = config["ffprobe_path"]
    temp_prefix = config["temp_prefix"]
    output_suffix = config["output_suffix"]
    gain_tolerance = config["gain_tolerance"]
    thread_id = threading.get_ident() 
    
    base_temp_name = f"{temp_prefix}{thread_id}_{os.path.basename(filename)}"
    temp_output_filename = base_temp_name + output_suffix + ".wav"
    
    # 包含所有可能产生的临时文件（包括 get_stereo_peaks_via_split 内部的）
    all_temps = [temp_output_filename, 
                 base_temp_name + config["left_suffix"] + ".wav", 
                 base_temp_name + config["right_suffix"] + ".wav"]

    def log_message(message):
        with print_lock:
            print(f"  [{os.path.basename(filename)}] {message}")
            
    def cleanup_temps():
        for f in all_temps:
            safe_remove(f, print_lock, filename)

    log_message("开始处理 (v3 - 分声道归一化)...")
    
    channels = get_audio_channels(filename, ffprobe_path, print_lock)
    if channels is None:
        log_message("错误：无法确定声道数。跳过。")
        cleanup_temps()
        return {'status': 'error', 'filename': filename, 'message': '无法获取声道数'}

    apply_gain = False
    ffmpeg_apply_cmd = None

    # === 单声道处理 ===
    if channels == 1:
        log_message("检测到单声道文件。正在检测峰值...")
        current_peak_db = get_mono_peak(filename, ffmpeg_path, print_lock)
        if current_peak_db is None:
            log_message("错误：无法检测单声道峰值。跳过。")
            cleanup_temps()
            return {'status': 'error', 'filename': filename, 'message': '无法检测单声道峰值'}
        
        log_message(f"  当前峰值: {current_peak_db:.2f} dB")
        gain_db = target_peak_db - current_peak_db
        gain_db_rounded = round(gain_db, 2)
        log_message(f"  计算得到增益: {gain_db_rounded:.2f} dB")
        
        if abs(gain_db_rounded) < gain_tolerance:
            log_message(f"  增益绝对值低于阈值 {gain_tolerance} dB。跳过处理。")
            cleanup_temps()
            return {'status': 'skipped', 'filename': filename, 'message': f'单声道增益低于阈值 {gain_tolerance}'}
        else:
            apply_gain = True
            gain_str = f"{gain_db_rounded:.2f}"
            ffmpeg_apply_cmd = [ffmpeg_path, "-y", "-i", filename, "-af", f"volume={gain_str}dB", temp_output_filename]

    # === 立体声处理 ===
    elif channels == 2:
        log_message("检测到立体声文件。正在检测峰值 (分离声道)...")
        stereo_peaks = get_stereo_peaks_via_split(filename, config, thread_id, print_lock)
        if stereo_peaks is None:
            log_message("错误：无法检测立体声峰值。跳过。")
            # get_stereo_peaks_via_split 内部会清理自己的临时文件
            safe_remove(temp_output_filename, print_lock, filename) # 清理主输出临时文件
            return {'status': 'error', 'filename': filename, 'message': '无法检测立体声峰值'}

        log_message(f"  检测到峰值: 左 {stereo_peaks['L']:.2f} dB, 右 {stereo_peaks['R']:.2f} dB")
        gain_l = target_peak_db - stereo_peaks['L']
        gain_r = target_peak_db - stereo_peaks['R']
        gain_l_rounded = round(gain_l, 2)
        gain_r_rounded = round(gain_r, 2)
        log_message(f"  计算得到增益: 左 {gain_l_rounded:.2f} dB, 右 {gain_r_rounded:.2f} dB")

        if abs(gain_l_rounded) < gain_tolerance and abs(gain_r_rounded) < gain_tolerance:
            log_message(f"  左右声道增益绝对值均低于阈值 {gain_tolerance} dB。跳过处理。")
            cleanup_temps()
            return {'status': 'skipped', 'filename': filename, 'message': f'立体声增益低于阈值 {gain_tolerance}'}
        else:
            apply_gain = True
            gain_l_str = f"{gain_l_rounded:.2f}"
            gain_r_str = f"{gain_r_rounded:.2f}"
            filter_complex = (f"channelsplit=channel_layout=stereo[FL][FR];"
                              f"[FL]volume={gain_l_str}dB[left];"
                              f"[FR]volume={gain_r_str}dB[right];"
                              f"[left][right]amerge=inputs=2")
            ffmpeg_apply_cmd = [ffmpeg_path, "-y", "-i", filename, "-filter_complex", filter_complex, temp_output_filename]

    # === 不支持的声道数 ===
    else:
        log_message(f"文件声道数为 {channels}，不支持。跳过。")
        cleanup_temps()
        return {'status': 'skipped', 'filename': filename, 'message': f'不支持的声道数: {channels}'}

    # --- 应用增益 ---
    if apply_gain and ffmpeg_apply_cmd:
        log_message("需要归一化，正在应用增益...")
        result_apply = run_command(ffmpeg_apply_cmd, timeout=300)
        time.sleep(0.1)

        if result_apply and result_apply.returncode == 0 and os.path.exists(temp_output_filename):
            log_message("处理成功，正在替换原始文件...")
            try:
                shutil.move(temp_output_filename, filename)
                log_message("文件已成功更新。")
                # cleanup_temps() 已在上面逻辑中包含，这里无需再次调用
                return {'status': 'processed', 'filename': filename, 'message': '处理成功'}
            except OSError as move_err:
                log_message(f"错误：无法替换文件。错误: {move_err}")
                log_message(f"临时文件 '{os.path.basename(temp_output_filename)}' 可能已保留。")
                cleanup_temps()
                return {'status': 'error', 'filename': filename, 'message': f'替换文件失败: {move_err}'}
        else:
            log_message(f"错误：ffmpeg 应用增益时失败 (返回码 {result_apply.returncode})。")
            log_message(f"  FFmpeg输出 (stderr): {result_apply.stderr[:500]}...")
            cleanup_temps()
            return {'status': 'error', 'filename': filename, 'message': 'ffmpeg应用增益失败'}
    else:
        # 如果因为跳过而没执行 apply_gain，这里不需要做什么，状态已在前面返回
        # 如果 ffmpeg_apply_cmd 因某种原因未设置，则是一个逻辑错误
        if apply_gain and not ffmpeg_apply_cmd:
             log_message("内部逻辑错误：标记为应用增益但命令未生成。")
             cleanup_temps()
             return {'status': 'error', 'filename': filename, 'message': '内部逻辑错误'}
        # 其他情况 (例如 apply_gain=False) 已经在前面返回 status 了
        pass 
        # 如果前面的逻辑都正确，这里应该不会被执行到
        # 但为了保险起见，返回一个通用错误
        cleanup_temps()
        return {'status': 'error', 'filename': filename, 'message': '未知的处理流程'}


# --- 主函数 (框架, 与v1基本相同, 只改标题和CONFIG) ---
def main():
    """主处理逻辑，使用线程池并行处理文件"""
    print("================================================")
    print(" 音频处理脚本 v3 (分声道归一化) - 多线程版")
    print("================================================")
    print()
    
    target_peak_str = input(f"请输入目标峰值 (例如 -4.5): ") # 示例值更新
    try:
        target_peak_db = float(target_peak_str)
    except ValueError:
        print(f"错误：输入 '{target_peak_str}' 不是有效的数字。程序将退出。")
        input("按 Enter 键退出...") 
        return 

    # 检查 ffprobe 是否可用
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
    print(f"结果统计：成功处理 {processed_count} 个文件，跳过 {skipped_count} 个文件，发生错误 {error_count} 个文件。")

# --- 程序入口 ---
if __name__ == "__main__":
    main()
    input("按 Enter 键退出...") 