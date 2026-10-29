(() => {
  // Clear marks and indices from any previous pass so stale labels cannot be clicked.
  document.querySelectorAll('[data-velo-mark]').forEach((mark) => mark.remove());
  document.querySelectorAll('[data-velo-interactive-index]').forEach((element) => element.removeAttribute('data-velo-interactive-index'));

  const inViewport = (box) => box.bottom > 0 && box.right > 0 && box.top < window.innerHeight && box.left < window.innerWidth;
  // Skip elements hidden behind something else, such as page content under a modal.
  const onTop = (element, box) => {
    const x = Math.min(Math.max(box.left + box.width / 2, 0), window.innerWidth - 1);
    const y = Math.min(Math.max(box.top + box.height / 2, 0), window.innerHeight - 1);
    const hit = document.elementFromPoint(x, y);
    return hit !== null && (hit === element || element.contains(hit));
  };
  const interactive = [...document.querySelectorAll('a, button, [role="button"], input, select, textarea')]
    .filter((element) => {
      const style = window.getComputedStyle(element);
      const box = element.getBoundingClientRect();
      // Disabled controls (such as Continue before a survey is answered) are not actionable, so they get no label.
      return !element.disabled && style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0 && inViewport(box) && onTop(element, box);
    });

  const registry = interactive.map((element, index) => {
    const label = index + 1;
    const box = element.getBoundingClientRect();
    const mark = document.createElement('span');
    mark.dataset.veloMark = String(label);
    mark.textContent = String(label);
    Object.assign(mark.style, { position: 'fixed', left: `${Math.max(box.left - 6, 0)}px`, top: `${Math.max(box.top - 24, 0)}px`, zIndex: '2147483647', background: '#ffcc00', color: '#000', border: '2px solid #000', borderRadius: '50%', width: '24px', height: '24px', textAlign: 'center', font: 'bold 14px sans-serif', lineHeight: '20px', pointerEvents: 'none' });
    document.body.appendChild(mark);
    // The attribute is the stable handle Playwright uses to re-locate this element.
    element.dataset.veloInteractiveIndex = String(label);
    return {
      label,
      selector: `[data-velo-interactive-index="${label}"]`,
      tag: element.tagName.toLowerCase(),
      // Radio buttons and checkboxes have no text of their own; use their <label>.
      text: (element.innerText || (element.labels && element.labels[0] && element.labels[0].innerText) || element.value || element.getAttribute('aria-label') || '').trim().slice(0, 80),
      checked: element.checked === true,
    };
  });
  return registry;
})();
