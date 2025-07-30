# -*- coding: utf-8 -*-
import os
import subprocess
import glob
import sys
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- 配置 ---
AUDIO_EXTENSIONS = ["*.mp3", "*.wav", "*.flac", "*.m4a"]
FFMPEG_PATH = "ffmpeg"
MAX_WORKERS = os.cpu_count()
# --- 配置结束 ---

def natural_sort_key(s):
    """为自然排序生成键"""
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split('([0-9]+)', s[0])]

def _run_ffmpeg_volumedetect(filepath, ffmpeg_filter_args):
    """内部辅助函数：运行 ffmpeg 并解析 volumedetect 输出"""
    command = [FFMPEG_PATH, "-hide_banner", "-i", filepath] + ffmpeg_filter_args + ["-f", "null", "-"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')
        max_volume_line = None
        if result.stderr:
            for line in result.stderr.splitlines():
                if "max_volume:" in line: max_volume_line = line.strip(); break
        if max_volume_line:
            parts = max_volume_line.split()
            try:
                idx = parts.index("max_volume:")
                if len(parts) > idx + 2: return parts[idx + 1], parts[idx + 2], None
                else: return None, None, f"无法解析行格式: {max_volume_line}"
            except (ValueError, IndexError): return None, None, f"解析行时出错: {max_volume_line}"
        else: return None, None, "未找到 'max_volume:' 信息"
    except FileNotFoundError: raise
    except Exception as e: return None, None, f"运行 ffmpeg 时发生错误: {e}"

def _get_overall_peak_task(filepath):
    """(线程任务函数) 获取单个文件的整体峰值"""
    main_error = None
    value, unit, error_msg = None, None, "未检测"
    try:
        value, unit, error_msg = _run_ffmpeg_volumedetect(filepath, ["-filter_complex", "volumedetect"])
    except FileNotFoundError:
        main_error = "ffmpeg_not_found"; error_msg = "ffmpeg 命令未找到"
    except Exception as e:
        main_error = f"处理时发生意外底层错误: {e}"; error_msg = main_error
    return filepath, value, unit, error_msg, main_error

def main():
    print("开始扫描当前目录下的音频文件...")
    start_time = time.time()

    files_to_process = []
    for ext_pattern in AUDIO_EXTENSIONS:
        found_files = glob.glob(ext_pattern)
        found_files.sort(key=lambda f: natural_sort_key((os.path.splitext(os.path.basename(f))[0],)))
        files_to_process.extend(found_files)
    # Optional: uncomment to sort mixed extensions
    # files_to_process.sort(key=lambda f: natural_sort_key((os.path.splitext(os.path.basename(f))[0],)))

    if not files_to_process:
        print(f"在当前目录下未找到任何匹配 {', '.join(AUDIO_EXTENSIONS)} 的文件。"); return

    total_files = len(files_to_process)
    worker_count_str = str(MAX_WORKERS) if MAX_WORKERS else "自动确定"
    print(f"共找到 {total_files} 个音频文件（已按名称排序），使用最多 {worker_count_str} 个线程开始并发处理...")

    results_data = {} # {filepath: (value, unit, error_msg, main_error)}
    futures_map = {}
    processed_count = 0
    ffmpeg_not_found_error_flag = False
    tasks_failed_count = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for filepath in files_to_process:
            future = executor.submit(_get_overall_peak_task, filepath)
            futures_map[future] = filepath

        print("正在处理文件...")
        for future in as_completed(futures_map):
            filepath = futures_map[future]
            file_failed = False
            try:
                fpath_result, value, unit, error_msg, main_error = future.result()
                results_data[fpath_result] = (value, unit, error_msg, main_error)
                if main_error == "ffmpeg_not_found":
                    if not ffmpeg_not_found_error_flag: print(f"\n错误: 无法找到 ffmpeg 命令。", file=sys.stderr)
                    ffmpeg_not_found_error_flag = True; file_failed = True
                elif main_error: print(f"\n严重错误: 处理 '{os.path.basename(filepath)}' 时出错: {main_error}", file=sys.stderr); file_failed = True
                elif error_msg: file_failed = True
            except Exception as e:
                print(f"\n严重错误: 处理 '{os.path.basename(filepath)}' 任务执行时意外: {e}", file=sys.stderr)
                results_data[filepath] = (None, None, f"任务执行异常: {e}", f"任务执行异常: {e}")
                file_failed = True
            if file_failed: tasks_failed_count += 1
            processed_count += 1
            progress = int(50 * processed_count / total_files); sys.stdout.write(f"\r[{'#' * progress}{'.' * (50 - progress)}] {processed_count}/{total_files} 完成"); sys.stdout.flush()

    # --- 结果输出 ---
    print("\n" + "=" * 20 + " Stereo Peak " + "=" * 20)
    count = 0
    success_count = 0
    for filepath in files_to_process:
        count += 1
        filename_without_ext = os.path.splitext(os.path.basename(filepath))[0]
        if filepath in results_data:
            value, unit, error_msg, main_error = results_data[filepath]
            display_error = main_error if main_error else error_msg
            if not display_error:
                # 使用 :03d 格式化编号
                print(f"{count:03d}. {filename_without_ext}: {value} {unit}")
                success_count += 1
            else:
                 # 使用 :03d 格式化编号
                 print(f"{count:03d}. {filename_without_ext}: 检测失败 ({display_error})")
        else:
            # 使用 :03d 格式化编号
            print(f"{count:03d}. {filename_without_ext}: 未找到处理结果")
            # 注意：如果文件不在 results_data 中，tasks_failed_count 在处理循环中可能未增加，这里补上
            if not file_failed: tasks_failed_count += 1

    end_time = time.time()
    print("-" * 50)

    if ffmpeg_not_found_error_flag:
        print("错误：处理过程中未能找到或执行 'ffmpeg' 命令。")
        print("请确保 FFmpeg 已正确安装并已将其路径添加到系统环境变量 PATH 中。")

    actual_success_count = total_files - tasks_failed_count
    print(f"处理总结：共 {total_files} 个文件。")
    print(f"  - 成功检测: {actual_success_count} 个")
    print(f"  - 检测失败: {tasks_failed_count} 个")
    print(f"总耗时: {end_time - start_time:.2f} 秒")

    try: input("\n按 Enter 键退出程序...")
    except EOFError: pass

if __name__ == "__main__":
    main()