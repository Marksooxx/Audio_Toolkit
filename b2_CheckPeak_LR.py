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

def _get_audio_peaks_task(filepath):
    """(线程任务函数) 获取整体、左、右声道峰值"""
    results = {'overall': (None, None, "未检测"), 'left': (None, None, "未检测"), 'right': (None, None, "未检测")}
    main_error = None
    try:
        overall_val, overall_unit, overall_err = _run_ffmpeg_volumedetect(filepath, ["-filter_complex", "volumedetect"])
        results['overall'] = (overall_val, overall_unit, overall_err)
        left_val, left_unit, left_err = _run_ffmpeg_volumedetect(filepath, ["-af", "pan=mono|c0=FL,volumedetect"])
        results['left'] = (left_val, left_unit, left_err)
        right_val, right_unit, right_err = _run_ffmpeg_volumedetect(filepath, ["-af", "pan=mono|c0=FR,volumedetect"])
        results['right'] = (right_val, right_unit, right_err)
    except FileNotFoundError:
        main_error = "ffmpeg_not_found"; err_msg = "ffmpeg 命令未找到"
        results = {'overall': (None, None, err_msg), 'left': (None, None, err_msg), 'right': (None, None, err_msg)}
    except Exception as e:
        main_error = f"处理时发生意外底层错误: {e}"; err_msg = main_error
        results = {'overall': (None, None, err_msg), 'left': (None, None, err_msg), 'right': (None, None, err_msg)}
    return filepath, results, main_error

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

    results_data = {} # {filepath: {'results': {...}, 'main_error': ...}}
    futures_map = {}
    processed_count = 0
    ffmpeg_not_found_error_flag = False
    tasks_failed_count = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for filepath in files_to_process:
            future = executor.submit(_get_audio_peaks_task, filepath)
            futures_map[future] = filepath

        print("正在处理文件...")
        for future in as_completed(futures_map):
            filepath = futures_map[future]
            file_failed = False
            try:
                fpath_result, results_dict, main_error = future.result()
                results_data[fpath_result] = {'results': results_dict, 'main_error': main_error}
                if main_error == "ffmpeg_not_found":
                    if not ffmpeg_not_found_error_flag: print(f"\n错误: 无法找到 ffmpeg 命令。", file=sys.stderr)
                    ffmpeg_not_found_error_flag = True; file_failed = True
                elif main_error: print(f"\n严重错误: 处理 '{os.path.basename(filepath)}' 时出错: {main_error}", file=sys.stderr); file_failed = True
                else:
                    for peak_type, (val, unit, err) in results_dict.items():
                        if err: file_failed = True; break # 子任务失败
            except Exception as e:
                print(f"\n严重错误: 处理 '{os.path.basename(filepath)}' 任务执行时意外: {e}", file=sys.stderr)
                results_data[filepath] = {'results': {'overall': (None, None, f"任务异常: {e}"), 'left': (None, None, f"任务异常: {e}"), 'right': (None, None, f"任务异常: {e}")}, 'main_error': f"任务执行异常: {e}"}
                file_failed = True
            if file_failed: tasks_failed_count += 1
            processed_count += 1
            progress = int(50 * processed_count / total_files); sys.stdout.write(f"\r[{'#' * progress}{'.' * (50 - progress)}] {processed_count}/{total_files} 完成"); sys.stdout.flush()

    # --- 结果输出 ---
    print("\n" + "=" * 20 + " Stereo Peak " + "=" * 20)
    count = 0
    for filepath in files_to_process:
        count += 1
        filename_without_ext = os.path.splitext(os.path.basename(filepath))[0]
        if filepath in results_data:
            result_info = results_data[filepath]
            if result_info['main_error']:
                 # 使用 :03d 格式化编号
                 print(f"{count:03d}. {filename_without_ext}: 检测失败 ({result_info['main_error']})")
            else:
                 overall_val, overall_unit, overall_err = result_info['results']['overall']
                 if not overall_err:
                     # 使用 :03d 格式化编号
                     print(f"{count:03d}. {filename_without_ext}: {overall_val} {overall_unit}")
                 else:
                     # 使用 :03d 格式化编号
                     print(f"{count:03d}. {filename_without_ext}: 检测失败 ({overall_err})")
        else:
            # 使用 :03d 格式化编号
            print(f"{count:03d}. {filename_without_ext}: 未找到处理结果")

    print("\n\n")
    print("=" * 20 + " LR Peak " + "=" * 20)
    count = 0
    files_with_any_success = 0
    for filepath in files_to_process:
        count += 1
        filename_without_ext = os.path.splitext(os.path.basename(filepath))[0]
        # 使用 :03d 格式化编号
        print(f"{count:03d}. {filename_without_ext}:") # 打印带编号的文件名

        if filepath in results_data:
            result_info = results_data[filepath]
            file_had_success = False
            if result_info['main_error']:
                 print(f"   Left: 检测失败 ({result_info['main_error']})")
                 print(f"   Right: 检测失败 ({result_info['main_error']})")
            else:
                left_val, left_unit, left_err = result_info['results']['left']
                if not left_err: print(f"   Left: {left_val} {left_unit}"); file_had_success = True
                else: print(f"   Left: 检测失败 ({left_err})")
                right_val, right_unit, right_err = result_info['results']['right']
                if not right_err: print(f"   Right: {right_val} {right_unit}"); file_had_success = True
                else: print(f"   Right: 检测失败 ({right_err})")
            if file_had_success: files_with_any_success += 1
        else:
            print(f"   Left: 未找到处理结果")
            print(f"   Right: 未找到处理结果")

    end_time = time.time()
    print("-" * 50)

    if ffmpeg_not_found_error_flag:
        print("错误：处理过程中未能找到或执行 'ffmpeg' 命令。")
        print("请确保 FFmpeg 已正确安装并已将其路径添加到系统环境变量 PATH 中。")

    fully_successful_files = total_files - tasks_failed_count
    print(f"处理总结：共 {total_files} 个文件。")
    print(f"  - 完全成功 (所有峰值检测成功): {fully_successful_files} 个")
    print(f"  - 部分或完全失败: {tasks_failed_count} 个")
    print(f"总耗时: {end_time - start_time:.2f} 秒")

    try: input("\n按 Enter 键退出程序...")
    except EOFError: pass

if __name__ == "__main__":
    main()