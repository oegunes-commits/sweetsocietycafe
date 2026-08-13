@echo off
cd /d C:\Users\osman\projects\sweetsociety-updater
set PYTHONIOENCODING=utf-8
echo ===== %date% %time% ===== >> update-log.txt
git pull -q origin main >> update-log.txt 2>&1
python tools\update_gallery.py >> update-log.txt 2>&1
git status --porcelain > "%TEMP%\ss_status.txt"
for %%A in ("%TEMP%\ss_status.txt") do set SIZE=%%~zA
if %SIZE% GTR 0 (
  git add -A >> update-log.txt 2>&1
  git commit -m "Daily gallery update (local)" >> update-log.txt 2>&1
  git push -q origin main >> update-log.txt 2>&1
  echo pushed >> update-log.txt
) else (
  echo no changes >> update-log.txt
)
