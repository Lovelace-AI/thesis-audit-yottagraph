<template>
    <v-container fluid class="fill-height d-flex align-center justify-center login-page">
        <v-card class="login-card text-center pa-8" elevation="0" max-width="400">
            <!-- Logo -->
            <div class="mb-6">
                <img src="/LL-logo-full-wht.svg" alt="Lovelace" class="login-logo" />
            </div>

            <!-- App Title -->
            <h1 class="app-title mb-8">{{ appName }}</h1>

            <!-- Error alert -->
            <v-alert v-if="authError" type="error" density="compact" variant="tonal" class="mb-4">
                {{ authError }}
            </v-alert>

            <!-- Server unavailable alert -->
            <v-alert
                v-if="serverStatus === 'unavailable'"
                type="warning"
                density="compact"
                variant="tonal"
                class="mb-4"
            >
                <v-icon icon="mdi-server-off" size="small" class="mr-1"></v-icon>
                Server is unavailable
            </v-alert>

            <!-- Login Button -->
            <v-btn
                :color="isLoginDisabled ? 'grey' : undefined"
                :text="loginButtonText"
                :disabled="isLoginDisabled"
                :loading="serverStatus === 'checking'"
                @click="redirectToLogin"
                variant="flat"
                size="large"
                block
                class="login-btn"
            >
            </v-btn>

            <!-- Version -->
            <p class="version-text mt-6">v{{ uiVersion }}</p>
        </v-card>
    </v-container>
</template>

<script lang="ts" setup>
    import { useServerStatus } from '~/composables/useServerStatus';

    const uiVersion = ref('(unknown)');
    const authError = ref<string | null>(null);
    const route = useRoute();

    // Get app configuration
    const { appName } = useAppInfo();

    // Get server status
    const { serverStatus, startChecking, stopChecking } = useServerStatus();

    // Computed properties
    const isLoginDisabled = computed(
        () => serverStatus.value === 'unavailable' || serverStatus.value === 'checking'
    );

    const loginButtonText = computed(() => {
        if (serverStatus.value === 'checking') {
            return 'CHECKING SERVER...';
        }
        if (serverStatus.value === 'unavailable') {
            return 'SERVER UNAVAILABLE';
        }
        return 'LOGIN';
    });

    onMounted(() => {
        // Start checking server status
        startChecking();

        // Check for error in query params
        if (route.query.error) {
            authError.value = route.query.error as string;
        }

        const versionString = useRuntimeConfig().public.versionString as string;
        if (versionString) {
            uiVersion.value = versionString;
        }

        const index = versionString.indexOf('release_');
        if (index !== -1) {
            uiVersion.value = versionString.slice(index + 8);
        }
    });

    onUnmounted(() => {
        // Stop checking when leaving the login page
        stopChecking();
    });

    async function redirectToLogin() {
        const audience = useRuntimeConfig().public.auth0Audience;
        const id = useRuntimeConfig().public.auth0ClientId;
        const url = useRuntimeConfig().public.auth0IssuerBaseUrl;

        const redirectUrl = `${useRequestURL().origin}/a0callback`;
        const loginUrl = `${url}/authorize?response_type=code&client_id=${id}&redirect_uri=${encodeURIComponent(redirectUrl)}&scope=openid%20profile%20email&audience=${audience}&prompt=login`;

        await navigateTo(loginUrl, {
            external: true,
            redirectCode: 302,
        });
    }
</script>

<style scoped>
    .login-page {
        background: var(--lv-black) !important;
        min-height: 100vh;
    }

    .login-card {
        background: transparent !important;
    }

    .login-logo {
        height: 2rem;
        width: auto;
    }

    .app-title {
        font-family: var(--font-headline);
        font-size: 2rem;
        font-weight: 400;
        color: var(--lv-white);
        margin: 0;
    }

    .login-btn {
        font-family: var(--font-mono);
        text-transform: uppercase;
        font-weight: 500;
        letter-spacing: 0.05em;
        background-color: var(--lv-green) !important;
        color: var(--lv-black) !important;
    }

    .login-btn:disabled {
        background-color: var(--lv-silver) !important;
        color: var(--lv-black) !important;
    }

    .version-text {
        color: var(--lv-silver);
        font-family: var(--font-mono);
        font-size: 0.75rem;
        margin: 0;
    }
</style>
