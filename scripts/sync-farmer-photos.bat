@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

echo.
echo ========================================
echo  Sync farmer photos to Supabase Storage
echo ========================================
echo.
echo Requires app server online with original photos.
echo.

python -c "from config.farmer_photo_sync import backfill_farmer_photos_to_supabase; import json; print(json.dumps(backfill_farmer_photos_to_supabase(), indent=2))"
pause
endlocal
