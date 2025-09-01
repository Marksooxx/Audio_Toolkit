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
from pathlib import Path 

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
        return subprocess.CompletedProcess(cmd_list, -1, stderr=f"エラー：コマンド '{cmd_list[0]}' が見つかりません")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd_list, -1, stderr=f"エラー：コマンドがタイムアウトしました ({timeout}秒)")
    except Exception as e:
        return subprocess.CompletedProcess(cmd_list, -1, stderr=f"エラー：コマンド実行中に不明なエラーが発生しました: {e}")


# --- 辅助函数：安全删除文件 (同v1) ---
def safe_remove(filepath, lock, filename_for_log):
    # ... (代码同 v1) ...
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except OSError as e:
        with lock: 
            print(f"  [{os.path.basename(filename_for_log)}] 警告：一時ファイル '{os.path.basename(filepath)}' を削除できませんでした。エラー: {e}")


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

    log_message("処理を開始します (v2 - ノーマライズ + 無音削除)...")

    # --- 阶段 1: 归一化处理 ---
    log_message("ステップ 1: ノーマライズ処理の確認...")
    
    # 1. 检测峰值
    volumedetect_cmd = [ffmpeg_path, "-i", filename, "-af", "volumedetect", "-f", "null", "-nostats", "-"]
    process_detect = run_command(volumedetect_cmd, timeout=60)
    if process_detect.returncode != 0 or "max_volume" not in process_detect.stderr.lower():
        log_message(f"エラー：音量を検出できません。後続の処理をスキップします。")
        cleanup_temps()
        return {'status': 'error', 'filename': filename, 'message': '音量を検出できません'}

    # 2. 解析峰值
    current_peak_db = None
    match = re.search(r"max_volume:\s*([-\d\.]+)\s*dB", process_detect.stderr, re.IGNORECASE)
    if match:
        try:
            current_peak_db = float(match.group(1))
            log_message(f"  ピークを検出: {current_peak_db:.2f} dB")
        except ValueError:
            log_message(f"  エラー：ピーク値を解析できません。後続の処理をスキップします。")
            cleanup_temps()
            return {'status': 'error', 'filename': filename, 'message': 'ピーク値を解析できません'}
    else:
        log_message("  エラー：max_volumeが見つかりません。後続の処理をスキップします。")
        cleanup_temps()
        return {'status': 'error', 'filename': filename, 'message': 'max_volumeが見つかりません'}

    # 3. 计算增益
    gain_db = target_peak_db - current_peak_db
    gain_db_rounded = round(gain_db, 2)
    gain_str = f"{gain_db_rounded:.2f}" 
    log_message(f"  計算されたゲイン: {gain_str} dB.")

    # 4. 应用增益 (如果需要)
    input_for_silence_removal = filename # 默认使用原文件进行静音删除
    normalization_applied = False
    
    if gain_str == "0.00":
        log_message("  ゲインは 0.00 dB です、ノーマライズのステップをスキップします。")
    else:
        apply_gain_cmd = [ffmpeg_path, "-y", "-i", filename, "-af", f"volume={gain_str}dB", temp_norm_filename]
        log_message(f"  ゲインを '{os.path.basename(temp_norm_filename)}' に適用中...")
        process_apply = run_command(apply_gain_cmd, timeout=300)
        time.sleep(0.1)

        if process_apply.returncode == 0 and os.path.exists(temp_norm_filename):
            log_message("  ノーマライズ成功。")
            input_for_silence_removal = temp_norm_filename # 后续静音处理使用这个文件
            normalization_applied = True
        else:
            log_message(f"  エラー：ゲインの適用に失敗しました (リターンコード {process_apply.returncode})。後続の無音削除をスキップします。")
            log_message(f"    FFmpeg出力: {process_apply.stderr[:500]}...")
            cleanup_temps()
            return {'status': 'error', 'filename': filename, 'message': 'ffmpegでのゲイン適用失敗'}

    # --- 阶段 2: 尾部静音删除 ---
    log_message("ステップ 2: 末尾の無音部分を削除しています...")
    silence_cmd = [
        ffmpeg_path, "-y",
        "-i", input_for_silence_removal, # 输入可能是原文件或归一化后的临时文件
        "-af", f"silenceremove=start_periods=0:stop_periods=-1:stop_duration={config['silence_stop_duration']}:stop_threshold={config['silence_stop_threshold']}",
        temp_silence_filename
    ]
    log_message(f"  '{os.path.basename(temp_silence_filename)}' へ無音削除を実行中...")
    process_silence = run_command(silence_cmd, timeout=300)
    time.sleep(0.1)

    if process_silence.returncode == 0 and os.path.exists(temp_silence_filename):
        log_message("  無音削除の処理が成功しました。元のファイルを置き換えています...")
        try:
            shutil.move(temp_silence_filename, filename)
            log_message("  ファイルは正常に更新されました。")
            # 清理可能存在的归一化临时文件
            if normalization_applied:
                 safe_remove(temp_norm_filename, print_lock, filename)
            
            status = 'processed' if normalization_applied else 'processed_silence_only' # 或用 skipped 更好?
            # 保持与 v1 一致，只要最终替换成功就算 processed
            return {'status': 'processed', 'filename': filename, 'message': '処理成功 (無音削除を含む)'} 
        except OSError as move_err:
            log_message(f"  エラー：最終ファイルを置き換えられませんでした。エラー: {move_err}")
            log_message(f"  一時ファイル '{os.path.basename(temp_silence_filename)}' が残っている可能性があります。")
            cleanup_temps() # 尝试清理所有临时文件
            return {'status': 'error', 'filename': filename, 'message': f'最終ファイルの置き換えに失敗: {move_err}'}
    else:
        log_message(f"  エラー：無音削除の実行に失敗しました (リターンコード {process_silence.returncode})。")
        log_message(f"    FFmpeg出力: {process_silence.stderr[:500]}...")
        cleanup_temps() # 清理所有临时文件
        # 如果归一化成功了但静音删除失败，原始文件没有被修改
        # 如果归一化没做，静音删除失败，原始文件也没变
        # 都算作错误
        return {'status': 'error', 'filename': filename, 'message': '無音削除に失敗'}

# --- 主函数 (框架, 与v1基本相同, 只改标题和CONFIG) ---
def main():
    """主处理逻辑，使用线程池并行处理文件"""
    print("================================================")
    print(" 音声処理スクリプト v2 (ノーマライズ+無音削除) - args")
    print("================================================")
    print(f"現在の作業ディレクトリ: {Path.cwd()}")
    print(f"当前已选择待处理文件：{len(sys.argv) - 1}个")
    print()
    
    target_peak_str = input(f"目標ピーク値を入力してください (例: -1): ")
    try:
        target_peak_db = float(target_peak_str)
    except ValueError:
        print(f"エラー：入力 '{target_peak_str}' は有効な数値ではありません。プログラムを終了します。")
        input("Enterキーを押して終了...") 
        return 

    max_workers = CONFIG["max_workers"]
    print(f"{max_workers} 個のスレッドを使用して並列処理します (システムの論理プロセッサ数に基づいて自動設定)。")
    print("-" * 20) 

    all_files = sys.argv[1:]
    wav_files = [f for f in all_files if not os.path.basename(f).startswith(CONFIG["temp_prefix"])]
    if not wav_files:
        print("現在のディレクトリに処理対象の .wav ファイルが見つかりません。")
        input("Enterキーを押して終了...") 
        return 
    print(f"{len(wav_files)} 個の .wav ファイルが見つかりました。処理を開始します...")

    processed_count = 0
    skipped_count = 0 # 这个版本跳过的概念不明确，统一用 processed/error
    error_count = 0
    print_lock = threading.Lock() 
    futures = [] 
    
    try:
        # 清理残留
        for f in glob.glob(CONFIG["temp_prefix"] + "*.wav"):
            safe_remove(f, print_lock, "起動時のクリーンアップ")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for filename in wav_files:
                future = executor.submit(process_file, filename, target_peak_db, CONFIG, print_lock)
                futures.append(future)
            
            print(f"{len(futures)} 個のタスクをスレッドプールに投入し、処理を開始します...")

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
                         print(f"ファイルの処理中に未捕捉の例外が発生しました: {exc}") 

    finally:
        # 最终清理
        print("-" * 20)
        print("最終クリーンアップを実行中...")
        final_cleaned_count = 0
        for temp_file in glob.glob(CONFIG["temp_prefix"] + "*.wav"):
            safe_remove(temp_file, print_lock, "最終クリーンアップ")
            final_cleaned_count += 1
        if final_cleaned_count > 0:
             print(f"最終クリーンアップが完了し、{final_cleaned_count} 個の残り一時ファイルを削除しました。")
        else:
             print("最終クリーンアップが完了し、残り一時ファイルはありませんでした。")

    print("-" * 20)
    print("すべてのファイルの処理が完了しました！")
    # V2 的 'skipped' 意义不大，主要看 processed 和 error
    print(f"結果：処理成功 {processed_count} ファイル、エラー {error_count} ファイル。")

# --- 程序入口 ---
if __name__ == "__main__":
    main()
    input("Enterキーを押して終了...")