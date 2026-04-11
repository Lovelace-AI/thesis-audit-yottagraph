const DEFAULT_MAX_ITERATIONS = 5;
const STORAGE_KEY = 'research:maxIterations';

const maxIterations = ref(DEFAULT_MAX_ITERATIONS);
let _initialized = false;

function loadFromStorage() {
    if (_initialized || typeof window === 'undefined') return;
    _initialized = true;
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
        const n = parseInt(stored, 10);
        if (!isNaN(n) && n >= 1 && n <= 20) {
            maxIterations.value = n;
        }
    }
    watch(maxIterations, (val) => {
        localStorage.setItem(STORAGE_KEY, String(val));
    });
}

export function useResearchSettings() {
    loadFromStorage();
    return {
        maxIterations,
        DEFAULT_MAX_ITERATIONS,
    };
}
