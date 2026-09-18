"""
点歌姬 - 独立控制窗口

把 display.html 打开成一个可聚焦的原生窗口，解决 OBS 浏览器源无法接收键盘输入的问题。

用法:
    1. 确保 NekoCha 正在运行（aiohttp 服务监听 5000 端口）
    2. pip install pywebview
    3. python plugins/song_request/launch_window.py

OBS 里用"窗口捕获"抓这个窗口的画面即可。
"""

import sys
import time
import threading
import webview

HOST = 'http://127.0.0.1:5000'


def get_display_url():
    """尝试自动发现第一个房间ID"""
    try:
        import urllib.request
        r = urllib.request.urlopen(HOST + '/api/rooms')
        import json
        data = json.loads(r.read())
        rooms = data.get('rooms', [])
        if rooms:
            room_id = rooms[0].get('room_id') or rooms[0].get('id')
            return f"{HOST}/rooms/{room_id}/plugins/song_request/display-page"
    except Exception:
        pass
    # 兜底：提示用户手动指定
    print('[点歌姬] 自动发现房间失败')
    print('[点歌姬] 请手动在下方 URL 中替换 {room_id}')
    return f"{HOST}/rooms/{{room_id}}/plugins/song_request/display-page"


def main():
    url = get_display_url()
    print(f'[点歌姬] 打开窗口: {url}')
    print('[点歌姬] 快捷键: ] 下一首  [ 上一首  Shift+X 全部恢复')

    # 先等一下，确保 aiohttp 服务起来了
    time.sleep(0.5)

    webview.create_window(
        title='点歌姬',
        url=url,
        width=360,
        height=520,
        min_size=(280, 400),
        frameless=False,
        text_select=False,
    )
    webview.start()


if __name__ == '__main__':
    main()
