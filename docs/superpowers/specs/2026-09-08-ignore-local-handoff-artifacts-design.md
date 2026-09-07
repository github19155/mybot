# 忽略本地交接产物设计

## 目标

让 Git 忽略当前工作区的两个本地协作产物：

- `.agent/handoff-subagent-fleet.md`
- `.fleet-patches/`

## 方案

在仓库根目录的 `.gitignore` 中增加这两个精确路径。保留本地文件，不删除、不移动，也不忽略整个 `.agent/` 目录。

## 非目标

- 不修改或删除已跟踪文件。
- 不把其他未跟踪文件加入版本控制。
- 不修改远程仓库或推送提交。
- 不使用仅当前机器生效的 `.git/info/exclude`，确保规则能随仓库共享。

## 验证

使用 `git check-ignore -v` 验证两个路径均命中新增规则，并确认 `git status --short` 不再显示它们；同时检查 `.gitignore` 之外没有意外修改。
