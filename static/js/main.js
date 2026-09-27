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

  // Dynamic Announcement Bar Dismiss
  const announcementBar = document.getElementById('announcementBar');
  const announcementClose = document.getElementById('announcementClose');
  const NOTICE_KEY = 'simts_notice_dismissed_v1';

  if (announcementBar && announcementClose) {
    if (sessionStorage.getItem(NOTICE_KEY) === 'true') {
      announcementBar.classList.add('dismissed');
    }

    announcementClose.addEventListener('click', () => {
      announcementBar.classList.add('dismissing');
      try {
        sessionStorage.setItem(NOTICE_KEY, 'true');
      } catch (e) {
        /* ignore storage access error */
      }
      setTimeout(() => {
        announcementBar.classList.add('dismissed');
      }, 360);
    });
  }
});