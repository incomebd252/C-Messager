(function () {
  try {
    var t = localStorage.getItem('cm-theme');
    if (t) document.documentElement.setAttribute('data-theme', t);
  } catch (e) {}

  // <form data-confirm="..."> submit korar age confirm dekhay
  document.addEventListener('submit', function (e) {
    var m = e.target.getAttribute('data-confirm');
    if (m && !window.confirm(m)) e.preventDefault();
  });
})();
