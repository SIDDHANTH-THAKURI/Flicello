// Small shared UI pieces: toasts (flash messages arrive as one and leave on their own).
export function toast(text) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.setAttribute('role', 'status');
  t.textContent = text;
  document.body.appendChild(t);
  dismiss(t);
}

export function dismiss(t) {
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 600); }, 5200);
  t.addEventListener('click', () => t.remove());
}
