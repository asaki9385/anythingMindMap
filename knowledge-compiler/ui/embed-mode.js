document.documentElement.dataset.embed =
  new URLSearchParams(location.search).get('embed') === '1' ? '1' : '0';
try {
  if (localStorage.getItem('kt-theme') === 'dark') {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
} catch (err) {}
