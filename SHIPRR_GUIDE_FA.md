# راه‌اندازی VodiWalker (نسخه‌ی Kata v19.2) روی Shiprr (shiprr.app)

## نکته‌های مهم درباره‌ی Shiprr
- **Dockerfile پشتیبانی نمی‌شود** → از `requirements.txt` + Start command استفاده می‌کنیم (`Dockerfile.txt` را نادیده بگیر).
- **دیسک دائمی ندارد** → با هر ری‌استارت/دیپلوی پوشه‌ی `data` پاک می‌شود. برای همین `persist_db.py` داده‌ها را در یک Postgres خارجی نگه می‌دارد.
- فقط **یک پورت HTTP** (همان `PORT`) از بیرون باز است. پروتکل‌های WS و xHTTP کار می‌کنند؛ ریلی TCP خام (پورت 6543) و پروکسی تلگرام روی پورت جدا از بیرون در دسترس نیست.
- ریپلیکا را روی **۱** نگه دار.

## مراحل
1. کد را (همین پوشه، بدون زیپ) در یک ریپوی **GitHub یا GitLab** آپلود کن؛ `app.py`، `main.py` و `requirements.txt` باید در ریشه‌ی ریپو باشند.
2. یک Postgres رایگان بساز (neon.tech یا supabase.com) و Connection string را کپی کن.
3. در shiprr.app: **Apps → New app** → ریپو را انتخاب کن → Region را نزدیک کاربرانت بگذار.
4. **Settings → Build & Runtime**:
   - Install command: `pip install -r requirements.txt`
   - Start command: `python app.py`
   - Health check path: `/health`
5. **Settings → Environment variables**: مقدارهای `env.example` را وارد کن (حداقل `ADMIN_USERNAME`، `ADMIN_PASSWORD`، `SECRET_KEY`، `DATABASE_URL`).
6. **Resources**: حداقل 1000 MHz و 512 MB (برای راحتی 1 GB).
7. Deploy بزن. بعد از بالا آمدن، آدرس `https://اسم-اپ.shiprr.dev` را در `PUBLIC_BASE_URL` بگذار (ذخیره = ری‌دیپلوی خودکار).
8. تست:
   - `/ping` → باید `pong` بدهد
   - `/health` → JSON با `status: ok`
   - `/login` → صفحه‌ی ورود پنل

## Ping / Keep-alive
- `/ping` و `/healthz` و `/health` بدون لاگین باز هستند (GET و HEAD).
- خود برنامه هر ۴ دقیقه (`KEEPALIVE_INTERVAL`) هم از مسیر آدرس عمومی و هم لوکال به `/ping` درخواست می‌زند. برای اطمینان بیشتر یک مانیتور UptimeRobot هم روی `/ping` بگذار.

## ذخیره‌سازی
- با ست بودن `DATABASE_URL`: هنگام استارت همه‌ی فایل‌ها از دیتابیس برمی‌گردند و هر ۱۵ ثانیه تغییرات ذخیره می‌شود (جدول `vw_files` خودکار ساخته می‌شود).
- اگر در لاگ دیدی `DATABASE NOT REACHABLE`، `DATABASE_URL` را درست کن و دوباره Deploy بزن.
