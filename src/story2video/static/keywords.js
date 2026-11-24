function toggleKw(btn) {
    var kw = btn.dataset.keyword;
    var sceneNum = btn.dataset.scene;
    var ta = document.querySelector('#prompt-section-' + sceneNum + ' textarea');
    if (!ta) return;

    if (btn.classList.contains('active')) {
        ta.value = ta.value.replace(kw, '').replace(/,\s*,/g, ',').replace(/,\s*$/, '').replace(/^\s*,/, '').trim();
        btn.classList.remove('active');
    } else {
        ta.value = ta.value.trim() + (ta.value.trim() ? ', ' : '') + kw;
        btn.classList.add('active');
    }
}

function syncKwChips(sceneNum) {
    var ta = document.querySelector('#prompt-section-' + sceneNum + ' textarea');
    if (!ta) return;
    var text = ta.value.toLowerCase();
    document.querySelectorAll('.kw-chip[data-scene="' + sceneNum + '"]').forEach(function(btn) {
        btn.classList.toggle('active', text.includes(btn.dataset.keyword.toLowerCase()));
    });
}

// Auto-sync chips on page load and after HTMX swaps
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.kw-chip').forEach(function(btn) {
        syncKwChips(btn.dataset.scene);
    });
});

document.addEventListener('htmx:afterSwap', function() {
    var scenes = new Set();
    document.querySelectorAll('.kw-chip').forEach(function(btn) {
        scenes.add(btn.dataset.scene);
    });
    scenes.forEach(function(s) { syncKwChips(s); });
});
