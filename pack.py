import os

# 用来自动打包成exe程序
def main():
    base = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(base, "wechat_gui.py")
    dist = os.path.join(base, "dist")
    cmd = f'pyinstaller.exe -Fw --noupx "{script}" --distpath "{dist}"'

    result = os.system(cmd)


if __name__ == '__main__':
    main()
