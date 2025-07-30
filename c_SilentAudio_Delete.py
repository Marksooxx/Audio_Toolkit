# -*- coding: utf-8 -*-
# remove_trailing_silence_mt.py
import os
import subprocess
import glob
import shutil
import sys
import time 
import threading 
from concurrent.futures import ThreadPoolExecutor, as_completed 

# --- 配置 ---
CONFIG = {
    "ffmpeg_path": "ffmpeg",
    "temp_prefix": "___temp_silence_only_thread_", # 临时文件前缀
    "max_workers": os.cpu_count() or 4, # 默认线程数
    # 静音删除参数 (只针对结尾)
    "silence_stop_duration": "1",       # 静音持续时间 (秒)
    "silence_stop_threshold": "-50dB"    # 静音阈值 (dB)
}

# --- 辅助函数：运行子进程 ---
def run_command(cmd_list, timeout=300): # 允许更长超时时间
    """执行命令行指令并返回结果，捕获输出"""
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
    """尝试删除文件，忽略不存在的错误，打印信息时加锁"""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except OSError as e:
        with lock: 
            print(f"  [{os.path.basename(filename_for_log)}] 警告：无法删除临时文件 '{os.path.basename(filepath)}'。错误: {e}")

# --- 单个文件处理函数 (核心逻辑) ---
def process_file(filename, config, print_lock):
    """处理单个 WAV 文件，移除结尾静音。"""
    ffmpeg_path = config["ffmpeg_path"]
    temp_prefix = config["temp_prefix"]
    thread_id = threading.get_ident() 
    
    # 构造临时文件名
    temp_output_filename = f"{temp_prefix}{thread_id}_{os.path.basename(filename)}"
    
    def log_message(message):
        with print_lock:
            print(f"  [{os.path.basename(filename)}] {message}")
            
    def cleanup_temps():
        safe_remove(temp_output_filename, print_lock, filename)

    log_message(f"开始处理结尾静音 (阈值 {config['silence_stop_threshold']}, 持续 {config['silence_stop_duration']}s)...")

    # 构建 ffmpeg 命令
    # start_periods=0: 不移除开头的静音
    # stop_periods=-1: 移除结尾处所有符合条件的静音段
    silence_cmd = [
        ffmpeg_path, "-y",
        "-i", filename, 
        "-af", f"silenceremove=start_periods=0:stop_periods=-1:stop_duration={config['silence_stop_duration']}:stop_threshold={config['silence_stop_threshold']}",
        temp_output_filename
    ]

    # 执行命令
    process_silence = run_command(silence_cmd, timeout=300) # 使用配置的超时
    time.sleep(0.1) # 短暂等待文件系统

    # 处理结果
    if process_silence.returncode == 0 and os.path.exists(temp_output_filename):
        # 检查文件大小是否有变化 (可选，仅供参考)
        try:
            original_size = os.path.getsize(filename)
            new_size = os.path.getsize(temp_output_filename)
            if original_size != new_size:
                 log_message("  检测到并处理了结尾静音。")
            else:
                 log_message("  未检测到符合条件的结尾静音或处理后无变化。")
        except OSError:
            log_message("  无法比较文件大小，继续操作。") # 获取大小失败

        log_message("  处理命令成功，正在替换原始文件...")
        try:
            shutil.move(temp_output_filename, filename)
            log_message("  文件已成功更新。")
            return {'status': 'processed', 'filename': filename, 'message': '处理成功'} 
        except OSError as move_err:
            log_message(f"  错误：无法替换文件。错误: {move_err}")
            log_message(f"  临时文件 '{os.path.basename(temp_output_filename)}' 可能已保留。")
            cleanup_temps() # 尝试清理
            return {'status': 'error', 'filename': filename, 'message': f'替换文件失败: {move_err}'}
    else:
        log_message(f"  错误：ffmpeg 执行静音删除失败 (返回码 {process_silence.returncode})。")
        log_message(f"    FFmpeg输出 (stderr): {process_silence.stderr[:500]}...")
        cleanup_temps() # 清理失败的临时文件
        return {'status': 'error', 'filename': filename, 'message': 'ffmpeg静音删除失败'}

# --- 主函数 (框架) ---
def main():
    """主处理逻辑，使用线程池并行处理文件"""
    print("=============================================")
    print(" 音频结尾静音移除脚本 - 多线程版")
    print("=============================================")
    print(f" 参数: 结尾静音 > {CONFIG['silence_stop_duration']}s 且 < {CONFIG['silence_stop_threshold']}")
    print()
    
    # 不需要用户输入参数，直接开始处理
    
    max_workers = CONFIG["max_workers"]
    print(f"将使用 {max_workers} 个线程并行处理 (根据系统逻辑处理器数量自动设定)。")
    print("-" * 20) 

    # 查找文件
    all_files = glob.glob("*.wav")
    wav_files = [f for f in all_files if not os.path.basename(f).startswith(CONFIG["temp_prefix"])]
    if not wav_files:
        print("当前目录下没有找到需要处理的 .wav 文件。")
        input("按 Enter 键退出...") 
        return 
    print(f"找到 {len(wav_files)} 个 .wav 文件准备处理...")

    # 初始化
    processed_count = 0
    error_count = 0
    print_lock = threading.Lock() 
    futures = [] 
    
    try:
        # 清理残留
        print("清理可能残留的上次运行的临时文件...")
        initial_cleanup_count = 0
        for f in glob.glob(CONFIG["temp_prefix"] + "*.wav"):
            safe_remove(f, print_lock, "启动清理")
            initial_cleanup_count +=1
        if initial_cleanup_count > 0: print(f"清理了 {initial_cleanup_count} 个文件。")

        # 创建并使用线程池
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for filename in wav_files:
                # 提交任务
                future = executor.submit(process_file, filename, CONFIG, print_lock)
                futures.append(future)
            
            print(f"已提交 {len(futures)} 个任务到线程池，开始处理...")

            # 获取结果
            for future in as_completed(futures):
                try:
                    result = future.result() 
                    if result['status'] == 'processed':
                        processed_count += 1
                    # 此脚本没有 'skipped' 状态
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
    # "Processed" 表示命令成功执行并替换了文件，不保证静音一定被移除
    print(f"结果统计：成功处理 {processed_count} 个文件，发生错误 {error_count} 个文件。")

# --- 程序入口 ---
if __name__ == "__main__":
    main()
    input("按 Enter 键退出...") 