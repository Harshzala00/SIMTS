document.addEventListener('DOMContentLoaded', () => {
  const nav = document.getElementById('siteNav');
  const toggle = document.getElementById('navToggle');
  if (toggle && nav) {
    toggle.addEventListener('click', () => {
      const open = nav.classList.toggle('open');
      toggle.setAttribute('aria-expanded', String(open));
    });
    nav.querySelectorAll('a').forEach(a => a.addEventListener('click', () => {
      nav.classList.remove('open');
      toggle.setAttribute('aria-expanded','false');
    }));
  }
  const adminMenu=document.getElementById('adminMenu');
  const sidebar=document.getElementById('adminSidebar');
  if(adminMenu && sidebar) adminMenu.addEventListener('click',()=>sidebar.classList.toggle('open'));
});