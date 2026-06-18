#!/usr/bin/env python3
import os


def extract_wechat_dict(
    wechat_app_path="/Applications/WeChat.app",
    output_path="str(WORKSPACE)/wechat_dict_5.bin",
):
    binary_path = os.path.join(wechat_app_path, "Contents/MacOS/WeChat")
    if not os.path.exists(binary_path):
        print(f"❌ 找不到 WeChat 二進制文件: {binary_path}")
        print("請確認微信已安裝在默認路徑，或手動修改 wechat_app_path 變量。")
        return

    print("🔍 正在掃描 WeChat 二進制文件尋找 zstd 字典 (這可能需要 10-20 秒)...")
    with open(binary_path, "rb") as f:
        data = f.read()

    keywords = [
        b"<msg>",
        b"<appmsg>",
        b"<title>",
        b"<des>",
        b"<url>",
        b"<thumburl>",
        b"<recorditem>",
        b"<fromusername>",
        b"<sender>",
        b"</msg>",
        b"</appmsg>",
        b"</title>",
        b"</des>",
        b"</url>",
        b"<type>",
        b"</type>",
        b"<showtype>",
        b"<content>",
    ]

    best_score = 0
    best_start = 0
    window_size = 8192

    for i in range(0, len(data) - window_size, 1024):
        window = data[i : i + window_size]
        score = sum(1 for kw in keywords if kw in window)
        if score > best_score:
            best_score = score
            best_start = i

    if best_score >= 6:
        dict_data = data[best_start : best_start + window_size]
        abs_output = os.path.abspath(output_path)
        with open(abs_output, "wb") as f:
            f.write(dict_data)
        print(f"✅ 成功提取候选字典到: {abs_output}")
        print(f"   (匹配關鍵詞得分: {best_score} / {len(keywords)})")
        print("👉 請將此路徑作為第 3 個參數傳入 export_mimi_html.py 進行驗證！")
    else:
        print(f"⚠️ 未找到高置信度的字典區域 (最高得分: {best_score})。")
        print("可能原因：微信版本過新導致結構變化，或微信未安裝在默認路徑。")


if __name__ == "__main__":
    extract_wechat_dict()
