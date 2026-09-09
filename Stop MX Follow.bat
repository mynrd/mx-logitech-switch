@echo off
for /f %%p in (%~dp0mx_follow.pid) do taskkill /PID %%p /F
