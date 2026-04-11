/**
 * Fetch all available data for an entity by NEID.
 *
 * Calls the gateway proxy for entity name, aliases, and full properties.
 * Returns the raw API data unmodified so the frontend can render it as-is.
 */

export default defineEventHandler(async (event) => {
    const neid = getRouterParam(event, 'neid');
    if (!neid || neid.length > 40 || !/^[\w-]+$/.test(neid)) {
        throw createError({ statusCode: 400, statusMessage: 'Invalid NEID' });
    }

    const paddedNeid = /^\d+$/.test(neid) ? neid.padStart(20, '0') : neid;
    const { public: config } = useRuntimeConfig();
    const gw = (config as any).gatewayUrl as string;
    const org = (config as any).tenantOrgId as string;
    const apiKey = (config as any).qsApiKey as string;

    if (!gw || !org) {
        throw createError({ statusCode: 503, statusMessage: 'Gateway not configured' });
    }

    const base = `${gw}/api/qs/${org}`;
    const headers: Record<string, string> = { 'X-Api-Key': apiKey };

    const [nameRes, aliasesRes, propsRes] = await Promise.allSettled([
        $fetch<{ name: string }>(`${base}/entities/${paddedNeid}/name`, { headers }),
        $fetch<{ aliases: string[] }>(`${base}/entities/${paddedNeid}/aliases`, { headers }),
        $fetch<any>(`${base}/elemental/entities/properties`, {
            method: 'POST',
            headers: { ...headers, 'Content-Type': 'application/x-www-form-urlencoded' },
            body: `eids=${encodeURIComponent(JSON.stringify([paddedNeid]))}&include_attributes=true`,
        }),
    ]);

    return {
        neid: paddedNeid,
        name: nameRes.status === 'fulfilled' ? nameRes.value.name || null : null,
        aliases: aliasesRes.status === 'fulfilled' ? (aliasesRes.value.aliases ?? []) : [],
        properties: propsRes.status === 'fulfilled' ? (propsRes.value.values ?? []) : [],
    };
});
