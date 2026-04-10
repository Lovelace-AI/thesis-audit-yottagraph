import { ref, reactive } from 'vue';

type ServerStatus = 'checking' | 'available' | 'unavailable' | 'not-configured';

interface ServerInfo {
    type: string;
    name: string;
    configKey: string;
    status: ServerStatus;
    address?: string;
    lastChecked?: Date;
    error?: string;
}

const server = reactive<ServerInfo>({
    type: 'query',
    name: 'Query API',
    configKey: 'queryServerAddress',
    status: 'checking',
});

let checkInterval: NodeJS.Timeout | null = null;

export function useServerStatus() {
    const config = useRuntimeConfig();

    async function checkServer() {
        try {
            const serverAddress = config.public[server.configKey] as string;

            if (!serverAddress) {
                console.log('[ServerStatus] No query server address configured');
                server.status = 'not-configured';
                server.address = undefined;
                return;
            }

            const baseURL = serverAddress.startsWith('http')
                ? serverAddress
                : `https://${serverAddress}`;

            server.address = baseURL;
            console.log('[ServerStatus] Checking query server at:', baseURL);

            await $fetch('/status', {
                baseURL,
                timeout: 5000,
            });

            server.status = 'available';
            server.error = undefined;
            server.lastChecked = new Date();
        } catch (error) {
            console.warn('[ServerStatus] Query server check failed:', error);
            server.status = 'unavailable';
            server.error = error instanceof Error ? error.message : 'Unknown error';
            server.lastChecked = new Date();
        }
    }

    function startChecking() {
        checkServer();
        if (!checkInterval) {
            checkInterval = setInterval(checkServer, 30000);
        }
    }

    function stopChecking() {
        if (checkInterval) {
            clearInterval(checkInterval);
            checkInterval = null;
        }
    }

    function getConfiguredServers() {
        return server.status !== 'not-configured' ? [server] : [];
    }

    const overallStatus = computed(() => server.status);

    return {
        servers: readonly(reactive({ query: server })),
        getConfiguredServers,
        serverStatus: overallStatus,
        checkServerStatus: checkServer,
        startChecking,
        stopChecking,
    };
}
