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
from pathlib import Path 

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
        return subprocess.CompletedProcess(cmd_list, -1, stderr=f"エラー：コマンド '{cmd_list[0]}' が見つかりません")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd_list, -1, stderr=f"エラー：コマンドがタイムアウトしました ({timeout}秒)")
    except Exception as e:
        return subprocess.CompletedProcess(cmd_list, -1, stderr=f"エラー：コマンド実行中に不明なエラーが発生しました: {e}")

# --- 辅助函数：安全删除文件 ---
def safe_remove(filepath, lock, filename_for_log):
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except OSError as e:
        with lock: 
            print(f"  [{os.path.basename(filename_for_log)}] 警告：一時ファイル '{os.path.basename(filepath)}' を削除できませんでした。エラー: {e}")

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

    log_message("処理を開始します (v1)...")

    # 1. 检测峰值
    volumedetect_cmd = [
        ffmpeg_path, "-i", filename, "-af", "volumedetect",
        "-f", "null", "-nostats", "-"
    ]
    process_detect = run_command(volumedetect_cmd, timeout=60)
    if process_detect.returncode != 0 or "max_volume" not in process_detect.stderr.lower():
        log_message(f"エラー：volumedetectの実行に失敗したか、音量情報が見つかりませんでした。")
        log_message(f"  FFmpeg出力 (stderr): {process_detect.stderr[:500]}...")
        safe_remove(temp_wav_filename, print_lock, filename)
        return {'status': 'error', 'filename': filename, 'message': '音量を検出できません'}

    # 2. 解析峰值
    current_peak_db = None
    match = re.search(r"max_volume:\s*([-\d\.]+)\s*dB", process_detect.stderr, re.IGNORECASE)
    if match:
        try:
            current_peak_db = float(match.group(1))
            log_message(f"現在のピークを検出: {current_peak_db:.2f} dB")
        except ValueError:
            log_message(f"エラー： '{match.group(1)}' からピーク値を解析できませんでした。")
            safe_remove(temp_wav_filename, print_lock, filename)
            return {'status': 'error', 'filename': filename, 'message': 'ピーク値を解析できません'}
    else:
        log_message("エラー：ffmpegの出力に 'max_volume' が見つかりませんでした。")
        safe_remove(temp_wav_filename, print_lock, filename)
        return {'status': 'error', 'filename': filename, 'message': 'max_volumeが見つかりません'}

    # 3. 计算增益
    gain_db = target_peak_db - current_peak_db
    gain_db_rounded = round(gain_db, 2)
    gain_str = f"{gain_db_rounded:.2f}" 
    log_message(f"調整が必要なゲインを計算しました: {gain_str} dB.")

    # 4. 应用增益
    if gain_str == "0.00":
        log_message("ゲインは 0.00 dB です、処理をスキップします。")
        safe_remove(temp_wav_filename, print_lock, filename)
        return {'status': 'skipped', 'filename': filename, 'message': 'ゲインが0です'}
    
    apply_gain_cmd = [
        ffmpeg_path, "-y", "-i", filename,
        "-af", f"volume={gain_str}dB",
        temp_wav_filename 
    ]
    log_message(f"ゲインを適用し、一時ファイル '{os.path.basename(temp_wav_filename)}' に出力しています...")
    process_apply = run_command(apply_gain_cmd, timeout=300)
    time.sleep(0.1) 

    if process_apply.returncode == 0 and os.path.exists(temp_wav_filename):
        log_message("処理成功、元のファイルを置き換えています...")
        try:
            shutil.move(temp_wav_filename, filename) 
            log_message("ファイルは正常に更新されました。")
            return {'status': 'processed', 'filename': filename, 'message': '処理成功'}
        except OSError as move_err:
            log_message(f"エラー：ファイルを置き換えられませんでした。エラーメッセージ: {move_err}")
            log_message(f"一時ファイル '{os.path.basename(temp_wav_filename)}' が残っている可能性がありますので、手動で処理してください。")
            return {'status': 'error', 'filename': filename, 'message': f'ファイルの置き換えに失敗: {move_err}'}
    else:
        log_message(f"エラー：ffmpegでのゲイン適用に失敗しました (リターンコード {process_apply.returncode})。")
        log_message(f"  FFmpeg出力 (stderr): {process_apply.stderr[:500]}...")
        safe_remove(temp_wav_filename, print_lock, filename) 
        return {'status': 'error', 'filename': filename, 'message': 'ffmpegでのゲイン適用失敗'}

# --- 主函数 (框架) ---
def main():
    """主处理逻辑，使用线程池并行处理文件"""
    print("=============================================")
    print(" 音声ノーマライズスクリプト v1 (基本ノーマライズ) - マルチスレッド版")
    print("=============================================")
    print(f"現在の作業ディレクトリ: {Path.cwd()}")
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
    skipped_count = 0
    error_count = 0
    print_lock = threading.Lock() 
    futures = [] 
    
    try:
        # 清理残留
        for f in glob.glob(CONFIG["temp_prefix"] + "*.wav"):
            safe_remove(f, print_lock, "起動時のクリーンアップ")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for filename in wav_files:
                # 提交任务
                future = executor.submit(process_file, filename, target_peak_db, CONFIG, print_lock)
                futures.append(future)
            
            print(f"{len(futures)} 個のタスクをスレッドプールに投入し、処理を開始します...")

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

    # 打印总结
    print("-" * 20)
    print("すべてのファイルの処理が完了しました！")
    print(f"結果：処理成功 {processed_count} ファイル、スキップ {skipped_count} ファイル、エラー {error_count} ファイル。")

# --- 程序入口 ---
if __name__ == "__main__":
    main()
    input("Enterキーを押して終了...")