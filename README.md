# الشبكة العربية الصغيرة

مجموعة منتقاة من المدونات والمواقع الشخصية باللغة العربية. مستوحاة من مشروع [Kagi Small Web](https://kagi.com/smallweb).

## التطوير المحلي

```bash
make venv      # إنشاء البيئة وتثبيت المكتبات
make fetch     # جلب الخلاصات
make build     # إنشاء الموقع
make preview   # فتح في المتصفح
make deploy    # تنظيف → جلب → بناء → فتح
make clean     # حذف الملفات المُنشأة
```

## أضف مدونتك

افتح [طلب إضافة](https://github.com/MohamedElashri/asw/issues/new?template=add-blog.yml) مع رابط الخلاصة واسم المدونة.

## بنية المشروع

```
├── sources.txt           # رابط خلاصة واحد في كل سطر
├── scripts/              # سكربتات Python
│   ├── fetch_feeds.py    # جلب الخلاصات
│   └── generate_site.py  # إنشاء الموقع
├── templates/            # قوالب Jinja2
├── static/               # CSS وأيقونات
├── public/               # الموقع المُنشأ (يُنشر)
└── .github/workflows/    # التحديث اليومي
```

## الرخصة

MIT
