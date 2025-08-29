# 将选中文件路径传给 Python 脚本（XYplorer 脚本指南）

本文总结了如何在 XYplorer 中调用 Python 脚本，并将当前选中的文件路径传递给 Python，以便在脚本内部使用 `sys.argv` 获取并处理这些路径。

---

## 一、基础语法与内置变量

- `run` 和 `run lax(...)`：用于在 XYplorer 中调用外部程序。
  - `lax` = “Run and **w**ait for **e**xternal program to terminate”（执行后等待程序结束）。
- 常用变量：
  - `<curitem>`：当前选中单个文件的完整路径。
  - `<selitems>`：所有选中项的路径集合，以空格分隔。

---

## 二、常见使用场景示例

### 1. 单个选中文件传参
最简单的调用示例：

```xys
run quote("C:\\Python\\python.exe") . ' ' . quote("C:\\path\\to\\script.py") . ' ' . quote("<curitem>");
```

Python 中使用：

```python
import sys
filepath = sys.argv[1]
print("Selected:", filepath)
```

**引用：** 官方论坛推荐这种写法，简洁有效。

---

### 2. 多个文件批量传参
一次性将所有选中文件路径作为参数传入：

```xys
run lax("C:\\Python\\python.exe" "C:\\path\\to\\script.py" <selitems>);
```

Python 中获取方式：

```python
import sys
paths = sys.argv[1:]
for path in paths:
    print("Processing:", path)
```

**引用：** 用户经验分享方法，非常简单实用。

---

### 3. 传当前目录 + 自定义参数
你还可以传递目录路径或其它自定义参数：

```xys
run lax("C:\\Python\\python.exe" "C:\\path\\to\\script.py" "<curpath>" "modeA" "123");
```

Python 内可以这样处理：

```python
import sys
curdir = sys.argv[1]
mode = sys.argv[2]
num = sys.argv[3]
```

---

## 三、完整按钮脚本示例

### 场景 A：处理单个选中文件
按钮里的脚本：

```xys
run quote("C:\\Python39\\python.exe") . ' ' . quote("D:\\MyScripts\\process.py") . ' ' . quote("<curitem>");
```

Python 脚本 `process.py` 中：

```python
import sys
sel = sys.argv[1]
print("Selected File:", sel)
```

---

### 场景 B：批量处理多个选中文件
按钮脚本：

```xys
run lax("C:\\Python39\\python.exe" "D:\\MyScripts\\batch_process.py" <selitems>);
```

Python 脚本：

```python
import sys
files = sys.argv[1:]
for f in files:
    print("Batch processing:", f)
```

---

## 四、小技巧与注意事项

- **路径中带空格**时，务必用 `quote()` 包裹参数，避免路径断裂。
- 使用 `lax()` 可以使 XYplorer 在 Python 脚本运行完毕后再继续后续操作。
- `<selitems>` 自动展开为多个参数，你可以在 Python 中直接用 `sys.argv[1:]` 接收列表。
- 若希望将多个路径拼在一个参数里（如用自定义分隔符），可使用 `get selectedItemsPathNames` 等高级变量，再在 Python 脚本中处理拆分。

---

##  总结表格

| 场景             | XYplorer 调用脚本写法                                                       | Python 接收方式                |
|------------------|-----------------------------------------------------------------------------|--------------------------------|
| 单个文件         | `run quote("python.exe") . ' ' . quote("script.py") . ' ' . quote("<curitem>");`     | `sys.argv[1]`                 |
| 多个选中文件     | `run lax("python.exe" "script.py" <selitems>);`                                        | `sys.argv[1:]` 列表形式        |
| 带目录 + 自定义参数 | `run lax("python.exe" "script.py" "<curpath>" "mode" "123");`                         | `sys.argv[1]`, `sys.argv[2]`, ... |

