// ==========================================
// MÓDULO MINHA CONTA (static/js/conta.js)
// ==========================================

async function renderContaView() {
  // Tenta sincronizar dados mais recentes com o servidor se possível
  if (typeof api === 'function') {
    try {
      const freshUser = await api('/api/auth/me');
      if (freshUser) {
        currentUser = freshUser;
        if (typeof updateSidebarUser === 'function') {
          updateSidebarUser();
        }
      }
    } catch (err) {
      console.warn('Não foi possível obter dados frescos do usuário:', err);
    }
  }

  updateProfileDisplay();

  // Configuração de senha para usuários cadastrados via Google Auth
  const groupCurrent = document.getElementById('group-current-password');
  const inputCurrent = document.getElementById('profile-current-password');
  if (groupCurrent && inputCurrent) {
    if (currentUser && currentUser.has_password === false) {
      groupCurrent.classList.add('hidden');
      groupCurrent.style.display = 'none';
      inputCurrent.removeAttribute('required');
    } else {
      groupCurrent.classList.remove('hidden');
      groupCurrent.style.display = '';
      inputCurrent.setAttribute('required', 'required');
    }
  }
}

function updateProfileDisplay() {
  const nameInput = document.getElementById('profile-name');
  const emailInput = document.getElementById('profile-email');
  const telegramInput = document.getElementById('profile-telegram');
  const avatarEl = document.getElementById('profile-avatar-lg');
  const displayNameEl = document.getElementById('profile-display-name');
  const displayEmailEl = document.getElementById('profile-display-email');
  const dataFeedback = document.getElementById('profile-data-feedback');
  const passFeedback = document.getElementById('profile-password-feedback');

  if (dataFeedback) {
    dataFeedback.textContent = '';
    dataFeedback.className = 'tg-feedback-box hidden';
  }
  if (passFeedback) {
    passFeedback.textContent = '';
    passFeedback.className = 'tg-feedback-box hidden';
  }

  if (currentUser) {
    const name = currentUser.name || '';
    const email = currentUser.email || '';
    const telegram = currentUser.telegram_chat_id || '';
    const initial = (name ? name[0] : (email ? email[0] : 'U')).toUpperCase();

    if (nameInput) nameInput.value = name;
    if (emailInput) emailInput.value = email;
    if (telegramInput) telegramInput.value = telegram;

    if (avatarEl) avatarEl.textContent = initial;
    if (displayNameEl) displayNameEl.textContent = name || email || 'Usuário';
    if (displayEmailEl) displayEmailEl.textContent = email;
  }
}

async function handleProfileDataSubmit(event) {
  event.preventDefault();
  const form = event.target;
  const feedback = document.getElementById('profile-data-feedback');
  const saveBtn = document.getElementById('btn-save-profile');

  if (feedback) {
    feedback.textContent = '';
    feedback.className = 'tg-feedback-box hidden';
  }

  const name = form.name.value.trim();
  const email = form.email.value.trim().toLowerCase();
  const telegram_chat_id = form.telegram_chat_id ? form.telegram_chat_id.value.trim() : null;

  if (saveBtn) {
    saveBtn.disabled = true;
    const span = saveBtn.querySelector('span');
    if (span) span.textContent = 'Salvando…';
  }

  try {
    const updatedUser = await api('/api/auth/profile', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name,
        email,
        telegram_chat_id: telegram_chat_id || null,
      }),
    });

    if (updatedUser) {
      currentUser = { ...currentUser, ...updatedUser };
    } else {
      currentUser = { ...currentUser, name, email, telegram_chat_id: telegram_chat_id || null };
    }

    if (typeof updateSidebarUser === 'function') {
      updateSidebarUser();
    }
    updateProfileDisplay();

    if (feedback) {
      feedback.textContent = 'Dados atualizados com sucesso!';
      feedback.className = 'tg-feedback-box success';
    }
  } catch (err) {
    if (feedback) {
      feedback.textContent = err.message || 'Erro ao atualizar dados. Verifique as informações.';
      feedback.className = 'tg-feedback-box error';
    }
  } finally {
    if (saveBtn) {
      saveBtn.disabled = false;
      const span = saveBtn.querySelector('span');
      if (span) span.textContent = 'Salvar Alterações';
    }
  }
}

async function handleProfilePasswordSubmit(event) {
  event.preventDefault();
  const form = event.target;
  const feedback = document.getElementById('profile-password-feedback');
  const saveBtn = document.getElementById('btn-save-password');

  if (feedback) {
    feedback.textContent = '';
    feedback.className = 'tg-feedback-box hidden';
  }

  const current_password = form.current_password ? form.current_password.value : '';
  const new_password = form.new_password.value;
  const confirm_password = form.confirm_password.value;

  if (new_password !== confirm_password) {
    if (feedback) {
      feedback.textContent = 'A confirmação da nova senha não confere.';
      feedback.className = 'tg-feedback-box error';
    }
    return;
  }

  const requiresCurrentPassword = !currentUser || currentUser.has_password !== false;
  if (requiresCurrentPassword && !current_password) {
    if (feedback) {
      feedback.textContent = 'Informe sua senha atual.';
      feedback.className = 'tg-feedback-box error';
    }
    return;
  }

  if (saveBtn) {
    saveBtn.disabled = true;
    const span = saveBtn.querySelector('span');
    if (span) span.textContent = 'Atualizando…';
  }

  try {
    const payload = { new_password };
    if (requiresCurrentPassword && current_password) {
      payload.current_password = current_password;
    }

    await api('/api/auth/profile', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    form.reset();

    if (currentUser && currentUser.has_password === false) {
      currentUser.has_password = true;
      const groupCurrent = document.getElementById('group-current-password');
      const inputCurrent = document.getElementById('profile-current-password');
      if (groupCurrent && inputCurrent) {
        groupCurrent.classList.remove('hidden');
        groupCurrent.style.display = '';
        inputCurrent.setAttribute('required', 'required');
      }
    }

    if (feedback) {
      feedback.textContent = 'Senha alterada com sucesso!';
      feedback.className = 'tg-feedback-box success';
    }
  } catch (err) {
    if (feedback) {
      feedback.textContent = err.message || 'Erro ao alterar senha. Verifique sua senha atual.';
      feedback.className = 'tg-feedback-box error';
    }
  } finally {
    if (saveBtn) {
      saveBtn.disabled = false;
      const span = saveBtn.querySelector('span');
      if (span) span.textContent = 'Atualizar Senha';
    }
  }
}

// ==========================================
// TOKEN DA EXTENSÃO DE NAVEGADOR
// ==========================================
async function handleGenerateExtensionToken() {
  const tokenInput = document.getElementById('extension-token-input');
  const feedback = document.getElementById('extension-token-feedback');
  const btn = document.getElementById('btn-generate-ext-token');

  if (feedback) {
    feedback.textContent = '';
    feedback.className = 'tg-feedback-box hidden';
  }

  if (btn) {
    btn.disabled = true;
    const span = btn.querySelector('span');
    if (span) span.textContent = 'Gerando token…';
  }

  try {
    const res = await api('/api/auth/extension-token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });

    const token = res && res.token ? res.token : '';
    if (tokenInput) {
      tokenInput.value = token;
      tokenInput.focus();
      tokenInput.select();
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
      try {
        await navigator.clipboard.writeText(token);
      } catch (clipErr) {
        console.warn('Clipboard writeText não permitido diretamente:', clipErr);
      }
    }

    if (feedback) {
      feedback.textContent = 'Token copiado! Cole na extensão do navegador.';
      feedback.className = 'tg-feedback-box success';
    }
  } catch (err) {
    if (feedback) {
      feedback.textContent = err.message || 'Erro ao gerar token da extensão.';
      feedback.className = 'tg-feedback-box error';
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      const span = btn.querySelector('span');
      if (span) span.textContent = 'Gerar e Copiar Token da Extensão';
    }
  }
}

async function handleContaTelegramTest() {
  const telegramInput = document.getElementById('profile-telegram');
  const feedback = document.getElementById('profile-data-feedback');
  const testBtn = document.getElementById('btn-conta-test-telegram');

  const val = telegramInput ? telegramInput.value.trim() : (currentUser ? currentUser.telegram_chat_id : '');

  if (!val) {
    if (feedback) {
      feedback.textContent = 'Informe e salve seu Telegram Chat ID antes de testar o envio.';
      feedback.className = 'tg-feedback-box error';
    }
    return;
  }

  if (feedback) {
    feedback.textContent = '';
    feedback.className = 'tg-feedback-box hidden';
  }

  if (testBtn) {
    testBtn.disabled = true;
    testBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Enviando…';
  }

  try {
    // Se o valor do input for diferente do salvo, salva antes
    if (!currentUser || currentUser.telegram_chat_id !== val) {
      const res = await api('/api/auth/telegram', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ telegram_chat_id: val }),
      });
      if (currentUser) {
        currentUser.telegram_chat_id = (res && res.telegram_chat_id !== undefined) ? res.telegram_chat_id : val;
      }
    }

    const testRes = await api('/api/auth/telegram/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });

    if (feedback) {
      feedback.textContent = (testRes && testRes.message)
        ? testRes.message
        : 'Mensagem de teste enviada com sucesso! Verifique seu Telegram.';
      feedback.className = 'tg-feedback-box success';
    }
  } catch (err) {
    if (feedback) {
      feedback.textContent = err.message || 'Falha ao enviar mensagem de teste. Verifique se o Chat ID está correto e se você deu /start no bot.';
      feedback.className = 'tg-feedback-box error';
    }
  } finally {
    if (testBtn) {
      testBtn.disabled = false;
      testBtn.innerHTML = '<i class="fa-solid fa-paper-plane" style="color: #229ED9"></i> Testar envio';
    }
  }
}

function formatTelegramStep1Text(user) {
  if (user && user.bot_username) {
    const cleanBot = String(user.bot_username).trim().replace(/^@/, '');
    return `Abra o nosso bot oficial <a href="https://t.me/${escapeHtml(cleanBot)}" target="_blank" rel="noopener noreferrer"><b>@${escapeHtml(cleanBot)}</b> <i class="fa-solid fa-arrow-up-right-from-square" style="font-size: 10px;"></i></a> no Telegram e clique em <b>Começar (<code>/start</code>)</b> para autorizar o envio de alertas.`;
  }
  if (user && user.bot_configured === false) {
    return `O administrador ainda não configurou o <code>TELEGRAM_BOT_TOKEN</code> no arquivo <code>.env</code> do servidor.`;
  }
  return `Abra o Telegram, procure pelo bot oficial do sistema e clique em <b>Começar (<code>/start</code>)</b> (isso autoriza o bot a te enviar mensagens).`;
}

async function openTelegramHelpModal() {
  const modal = document.getElementById('modal-telegram-help');
  const step1Text = document.getElementById('tg-help-step-1-text');

  if (currentUser && !currentUser.bot_username && typeof api === 'function') {
    try {
      const fresh = await api('/api/auth/me');
      if (fresh) currentUser = { ...currentUser, ...fresh };
    } catch (err) {}
  }

  if (step1Text) {
    step1Text.innerHTML = formatTelegramStep1Text(currentUser);
  }

  if (modal) {
    modal.classList.remove('hidden');
  }
}

function closeTelegramHelpModal() {
  const modal = document.getElementById('modal-telegram-help');
  if (modal) {
    modal.classList.add('hidden');
  }
}

function openExtensionHelpModal() {
  const modal = document.getElementById('modal-extension-help');
  if (modal) {
    modal.classList.remove('hidden');
  }
}

function closeExtensionHelpModal() {
  const modal = document.getElementById('modal-extension-help');
  if (modal) {
    modal.classList.add('hidden');
  }
}

window.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeTelegramHelpModal();
    closeExtensionHelpModal();
  }
});
