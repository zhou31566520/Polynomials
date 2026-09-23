import libtorrent as lt
import requests
import os
import time


def download_torrent_file(url: str, save_torrent_path: str):
    """HTTP下载 .torrent 种子文件"""
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    with open(save_torrent_path, "wb") as f:
        f.write(resp.content)
    print(f"种子文件已保存: {save_torrent_path}")

def bt_download(torrent_path: str, output_dir: str):
    """加载本地torrent，P2P下载内容"""
    os.makedirs(output_dir, exist_ok=True)

    # 创建session会话
    ses = lt.session()
    ses.listen_on(6881, 6891)

    # 开启DHT、PEX，寻找更多节点，国内网络很重要
    settings = ses.get_settings()
    settings["enable_dht"] = True
    # settings["enable_pex"] = True
    settings["dht_bootstrap_nodes"] = "router.bittorrent.com:6881,router.utorrent.com:6881"
    ses.apply_settings(settings)

    ti = lt.torrent_info(torrent_path)
    handle = ses.add_torrent({
        "ti": ti,
        "save_path": output_dir,
        "storage_mode": lt.storage_mode_t(1),  # 稀疏文件，不预先占满磁盘
    })

    print("等待获取元信息……")
    while not handle.has_metadata():
        time.sleep(0.5)

    # 打印种子内包含的全部文件
    info = handle.get_torrent_info()
    print("\n种子包含文件列表：")
    for idx, f in enumerate(info.files()):
        size_mb = f.size / (1024*1024)
        print(f"[{idx}] {f.path}  |  {size_mb:.2f} MB")

    print("\n开始BT下载……")
    while True:
        stat = handle.status()
        progress_pct = stat.progress * 100
        down_mb = stat.download_rate / (1024*1024)
        up_mb = stat.upload_rate / (1024*1024)

        states = [
            "排队", "校验文件", "获取元数据",
            "下载中", "完成", "做种中", "分配磁盘空间", "快速校验"
        ]
        state_txt = states[stat.state]

        print(f"\r进度 {progress_pct:.2f}% | ↓{down_mb:.2f} MB/s ↑{up_mb:.2f} MB/s | {state_txt}", end="")

        if stat.is_seeding:
            print("\n✅全部文件下载完成！")
            break
        time.sleep(1)

if __name__ == "__main__":
    # ========== 修改这里为你的torrent下载地址 ==========
    torrent_file_path = r"C:\Users\Lenovo\Downloads\kali-linux-2026.2-live-everything-amd64.iso"
    save_path = r"G:"

    # TORRENT_URL = "https://cdimage.kali.org/current/kali-linux-2026.2-live-amd64.iso.torrent"
    # TORRENT_SAVE = r"./kali.torrent"
    # OUTPUT_FOLDER = r"D:\kali_out"

    # 1.下载torrent种子小文件
    # download_torrent_file(TORRENT_URL, TORRENT_SAVE)
    # 2.BT下载iso
    bt_download(torrent_file_path, save_path)
