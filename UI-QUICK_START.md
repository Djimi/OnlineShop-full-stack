# 🚀 Quick Start - React Frontend

## 30-Second Setup

### Terminal 1: Start Full Stack
```bash
docker compose up -d --build
```

This builds the Auth, Items, API Gateway, and frontend images from the current source, then starts the databases and supporting infrastructure.

### Browser
Open: **http://localhost:5173**

---

## 📋 Test User Credentials

After registering:
- **Username**: `testuser`
- **Password**: `Test@1234`

Or use any credentials to register a new account.

---

## 🎯 User Flow

1. **Home** - Landing page with CTA buttons
2. **Register** - Create new account
3. **Login** - Authenticate with credentials
4. **Catalog** - Browse 5 sample products
5. **Details** - View full product information
6. **Logout** - Return to home

---

## 📱 Test Responsiveness

Open DevTools (F12) and toggle device toolbar:
- **Mobile**: 375px (iPhone)
- **Tablet**: 768px (iPad)
- **Desktop**: 1920px (Full screen)

All layouts respond beautifully!

---

## 🔧 Available Commands

```bash
npm run dev       # Development server (http://localhost:5173)
npm run build     # Production build
npm run preview   # Preview production build
npm run lint      # Check code quality
```

---

## 🐛 Common Issues

| Problem | Solution |
|---------|----------|
| Port 5173 in use | Vite auto-uses next port |
| Backend error | Run `docker compose ps` to check services |
| Build fails | `rm -rf node_modules && npm install` |
| Token expired | Log out and log back in |

---

## 📂 Key Files

- `src/App.tsx` - Root component
- `src/routes/index.tsx` - Page routes
- `src/pages/` - All pages
- `src/components/` - Reusable components
- `src/services/api.ts` - API configuration

---

## ✨ Features

✅ Authentication (Register/Login)
✅ Protected routes
✅ Product catalog
✅ Product details
✅ Responsive design
✅ Error handling
✅ Loading states
✅ Toast notifications
✅ Modern UI
✅ TypeScript

---

## 📚 Documentation

- `FRONTEND_SETUP.md` - Complete setup guide
- `FRONTEND_IMPLEMENTATION_SUMMARY.md` - Detailed overview
- `README.md` - Project info (in frontend/)

---

## 🎨 Design

- **Framework**: Tailwind CSS v4
- **Icons**: Lucide React
- **Colors**: Blue primary, Gray secondary
- **Layout**: Responsive grid system
- **Accessibility**: Full WCAG compliance

---

## 🔐 Security

- JWT token authentication
- Token stored in localStorage + Zustand
- Protected routes
- Auto logout on 401
- Form validation
- HTTPS ready

---

## 💡 Tips

1. Use DevTools Network tab to watch API calls
2. Check Console for any errors
3. Use React DevTools extension for debugging
4. Tailwind classes are utility-based
5. All components are in `src/components/`

---

## 📞 Need Help?

1. Check `FRONTEND_SETUP.md` for detailed guide
2. Review error messages in Console
3. Check backend services status: `docker compose ps`
4. Verify API Gateway is running on port 10000

---

**Everything is ready! Start with Terminal 1 command above.** 🎉
