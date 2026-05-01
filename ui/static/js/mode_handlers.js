window.K8ModeHandlers = {
  'k8-info': {
    clearNamespace: false,
    welcome: 'k8-info loaded fresh. Select namespace, then ask pods/deployments/services/secrets/quota/HPA/ingress questions.'
  },
  'k8-agent': {
    clearNamespace: false,
    welcome: 'k8-agent loaded fresh. Select namespace, then ask deployment edits or rollout actions.'
  },
  'k8-autofix': {
    clearNamespace: false,
    showWelcomeInChat: false,
    welcome: 'k8-autofix loaded fresh. Select namespace to view Datadog issues and run suggested autofix actions.'
  }
};
