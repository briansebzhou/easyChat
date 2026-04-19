import sys
import time
import datetime
import threading
import keyboard

from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *


# 定时发送子线程类
class ClockThread(QThread):
    # 定义信号：用于通知GUI显示错误信息
    error_signal = pyqtSignal(str)
    # 触发GUI线程发送消息（跨线程安全）
    send_signal = pyqtSignal(int, int)
    # 触发GUI线程执行防止掉线
    prevent_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        # 是否正在定时
        self.time_counting = False
        # 发送信息的函数（保留作为回退，正常情况下用 send_signal）
        self.send_func = None
        # 定时列表（QListWidget 引用，仅在主线程访问；worker 用 _schedules 快照）
        self.clocks = None
        # 是否防止自动下线
        self.prevent_offline = False
        self.prevent_func = None
        # 每隔多少分钟进行一次防止自动下线操作
        self.prevent_count = 60

        # 新增：用于存储已执行过的任务标识，防止重复执行
        self.executed_tasks = set()

        # 用于防止掉线的内部计时器
        self._prevent_timer = 0

        # worker 线程读取的定时任务快照（list[str]），由主线程通过 set_schedules 更新
        self._schedules = []
        self._schedules_lock = threading.Lock()

    def set_schedules(self, items):
        """主线程调用：刷新 worker 线程使用的任务快照。"""
        with self._schedules_lock:
            self._schedules = list(items)

    def _snapshot_schedules(self):
        with self._schedules_lock:
            return list(self._schedules)

    def __del__(self):
        self.wait()

    def run(self):
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            # 初始化防止掉线的计时器，设置为 prevent_count 分钟对应的秒数
            self._prevent_timer = self.prevent_count * 60

            while self.time_counting:
                now = datetime.datetime.now()
                next_event_time = None

                # --- 1. 遍历快照，查找最近的下一个闹钟时间 ---
                schedules = self._snapshot_schedules()
                try:
                    for task_id in schedules:
                        # 如果任务已经执行过，则跳过
                        if task_id in self.executed_tasks:
                            continue

                        parts = task_id.split(" ")
                        clock_str = " ".join(parts[:5])
                        dt_obj = datetime.datetime.strptime(clock_str, "%Y %m %d %H %M")

                        # 取所有未执行任务里时间最近的一个（含已过去但还在60秒窗口内的）
                        if next_event_time is None or dt_obj < next_event_time:
                            next_event_time = dt_obj
                except Exception as e:
                    # 解析任务字符串失败时上报
                    error_msg = f"读取闹钟列表时出错: {e}"
                    print(error_msg)
                    # 停止定时任务
                    self.time_counting = False
                    # 发送信号通知GUI显示错误
                    self.error_signal.emit(error_msg)
                    return

                # --- 2. 计算休眠时间 ---
                # 没有任何未来任务时，休眠60秒后再检查，避免忙循环占用CPU
                sleep_seconds = 60

                if next_event_time:
                    delta = (next_event_time - now).total_seconds()
                    # 确保休眠时间不为负，且至少检查一次
                    sleep_seconds = max(0, delta)
                    # 上限60秒，避免错过用户中途新增的更早任务
                    sleep_seconds = min(sleep_seconds, 60)

                print(sleep_seconds)

                # --- 3. 整合“防止掉线”的逻辑 ---
                if self.prevent_offline:
                    # 取“下一个闹钟”和“下一次防掉线”中更早发生的一个
                    sleep_seconds = min(sleep_seconds, self._prevent_timer)

                # --- 4. 执行休眠 ---
                # sleep_seconds 可能是小数，time.sleep支持
                time.sleep(sleep_seconds)

                # 更新防止掉线的内部计时器
                self._prevent_timer -= sleep_seconds
                if self._prevent_timer <= 0:
                    self._prevent_timer = 0  # 避免变为很大的负数

                # --- 5. 休眠结束，检查并执行到期的任务 ---
                now = datetime.datetime.now()  # 获取唤醒后的精确时间

                # 检查并执行到期的闹钟（使用快照，避免跨线程访问 QListWidget）
                schedules = self._snapshot_schedules()
                try:
                    for task_id in schedules:
                        print(task_id)
                        if task_id in self.executed_tasks:
                            continue

                        parts = task_id.split(" ")
                        st_ed = parts[5]
                        st, ed = st_ed.split('-')
                        clock_str = " ".join(parts[:5])
                        dt_obj = datetime.datetime.strptime(clock_str, "%Y %m %d %H %M")

                        # 只执行刚刚到期的任务（时间窗口：60秒内）
                        time_diff = (now - dt_obj).total_seconds()
                        if 0 <= time_diff <= 60:
                            # 通过信号在GUI线程执行发送，避免跨线程访问 Qt 控件
                            self.send_signal.emit(int(st), int(ed))
                            # 记录为已执行
                            self.executed_tasks.add(task_id)
                        elif time_diff > 60:
                            # 超过60秒的任务标记为已过期，不再执行
                            self.executed_tasks.add(task_id)

                except Exception as e:
                    error_msg = f"执行任务时解析闹钟出错: {e}"
                    print(error_msg)
                    # 停止定时任务
                    self.time_counting = False
                    # 发送信号通知GUI显示错误
                    self.error_signal.emit(error_msg)
                    return

                # 检查并执行防止掉线
                if self.prevent_offline and self._prevent_timer <= 0:
                    # 通过信号在GUI线程执行，避免 uiautomation 与 Qt 跨线程问题
                    self.prevent_signal.emit()
                    # 重置计时器
                    self._prevent_timer = self.prevent_count * 60


class MyListWidget(QListWidget):
    """支持双击可编辑的QListWidget"""
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)  # 设置选择多个

        # 双击可编辑
        self.edited_item = self.currentItem()
        self.close_flag = True
        self.doubleClicked.connect(self.item_double_clicked)
        self.currentItemChanged.connect(self.close_edit)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        """回车事件，关闭edit"""
        super().keyPressEvent(e)
        if e.key() == Qt.Key_Return:
            if self.close_flag:
                self.close_edit()
            self.close_flag = True

    def edit_new_item(self) -> None:
        """edit一个新的item"""
        self.close_flag = False
        self.close_edit()
        count = self.count()
        self.addItem('')
        item = self.item(count)
        self.edited_item = item
        self.openPersistentEditor(item)
        self.editItem(item)

    def item_double_clicked(self, modelindex: QModelIndex) -> None:
        """双击事件"""
        self.close_edit()
        item = self.item(modelindex.row())
        self.edited_item = item
        self.openPersistentEditor(item)
        self.editItem(item)

    def close_edit(self, *_) -> None:
        """关闭edit"""
        if self.edited_item and self.isPersistentEditorOpen(self.edited_item):
            self.closePersistentEditor(self.edited_item)


class MultiInputDialog(QDialog):
    """
    用于用户输入的输入框，可以根据传入的参数自动创建输入框
    """
    def __init__(self, inputs: list, default_values: list = None, parent=None) -> None:
        """
        inputs: list, 代表需要input的标签，如['姓名', '年龄']
        default_values: list, 代表默认值，如['张三', '18']
        """
        super().__init__(parent)
        
        layout = QVBoxLayout(self)
        self.inputs = []
        for n, i in enumerate(inputs):
            layout.addWidget(QLabel(i))
            input = QLineEdit(self)

            # 设置默认值
            if default_values is not None:
                input.setText(default_values[n])

            layout.addWidget(input)
            self.inputs.append(input)
            
        ok_button = QPushButton("确认")
        ok_button.clicked.connect(self.accept)
        
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        
        button_layout = QHBoxLayout()
        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)
    
    def get_input(self):
        """获取用户输入"""
        return [i.text() for i in self.inputs]


class FileDialog(QDialog):
    """
    文件选择框
    """
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.inputs = []
        layout = QVBoxLayout(self)
        
        layout.addWidget(QLabel("请指定发送给哪些用户(1,2,3代表发送给前三位用户)，如需全部发送请忽略此项"))
        input = QLineEdit(self)
        layout.addWidget(input)
        self.inputs.append(input)
        
        # 选择文件
        choose_layout = QHBoxLayout()

        path = QLineEdit(self)
        choose_layout.addWidget(path)
        self.inputs.append(path)

        file_button = QPushButton("选择文件")
        file_button.clicked.connect(self.select)
        choose_layout.addWidget(file_button)

        layout.addLayout(choose_layout)
        
        # 确认按钮
        ok_button = QPushButton("确认")
        ok_button.clicked.connect(self.accept)

        # 取消按钮
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)

        # 按钮布局
        button_layout = QHBoxLayout()
        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)
    
    def select(self):
        path_input = self.inputs[1]
        # 修改为支持多文件选择
        paths = QFileDialog.getOpenFileNames(self, '打开文件', '/home')[0]
        if paths:
            # 将多个文件路径用分号连接显示
            path_input.setText(";".join(paths))
    
    def get_input(self):
        """获取用户输入"""
        return [i.text() for i in self.inputs]


class MyDoubleSpinBox(QWidget):
    def __init__(self, desc: str, **kwargs):
        """
        附带标签的DoubleSpinBox，支持小数输入
        Args:
            desc: 默认的标签
        """
        super().__init__(**kwargs)

        layout = QHBoxLayout()

        self.desc = desc
        self.label = QLabel(desc)

        self.spin_box = QDoubleSpinBox()
        self.spin_box.setDecimals(1)
        self.spin_box.setSingleStep(0.1)
        self.spin_box.setRange(0.0, 60.0)

        layout.addWidget(self.label)
        layout.addWidget(self.spin_box)
        self.setLayout(layout)


class MySpinBox(QWidget):
    def __init__(self, desc: str, **kwargs):
        """
        附带标签的SpinBox
        Args:
            desc: 默认的标签
        """
        super().__init__(**kwargs)

        layout = QHBoxLayout()

        # 初始化标签
        self.desc = desc
        self.label = QLabel(desc)
        # self.label.setAlignment(Qt.AlignCenter)

        # 初始化计数器
        self.spin_box = QSpinBox()
        # self.spin_box.valueChanged.connect(self.valuechange)

        layout.addWidget(self.label)
        layout.addWidget(self.spin_box)
        self.setLayout(layout)

    # def valuechange(self):
    #     self.label.setText(f"{self.desc}: {self.spin_box.value()}")