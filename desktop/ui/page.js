'use strict';
/* The only page that ships inside the shell: it is shown when the app's core could not be started, and says
   why. The shell hands over the reason as window.LMV_PROBLEM before this runs. It is put on the page as
   text, never as markup. */
document.getElementById('detail').textContent = typeof window.LMV_PROBLEM === 'string' ? window.LMV_PROBLEM : '';
