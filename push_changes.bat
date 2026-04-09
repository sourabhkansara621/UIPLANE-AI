@echo off
echo Checking git status...
git status

echo.
echo Staging all changes...
git add .

echo.
echo Committing changes...
git commit -m "feat: Add local fonts, improve UI/UX, implement logout session management, add comprehensive documentation

- Download and serve Google Fonts (JetBrains Mono, Syne) locally from /static/fonts
- Fix login form font clipping issue by changing title to monospace font
- Implement proper logout with JWT token blacklist for session termination
- Clear chat messages on fresh login for clean user experience
- Clear username/password fields after logout
- Add comprehensive inline comments and docstrings to all Python backend files:
  - Authentication system (JWT, RBAC, auth service)
  - API routers (auth, chat, k8s)
  - Kubernetes gateway and capabilities
  - AI agents
- Improve code maintainability with detailed function documentation

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"

echo.
echo Pushing to GitHub...
git push origin main

echo.
echo Done!
pause
