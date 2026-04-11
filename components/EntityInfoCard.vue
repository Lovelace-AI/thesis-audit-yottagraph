<template>
    <v-card v-if="neid" variant="outlined" class="entity-info-card">
        <v-card-title class="entity-info-header">
            <div class="entity-info-title-row">
                <v-icon size="small" class="mr-2">mdi-database-search-outline</v-icon>
                <span v-if="entityData?.name" class="entity-name">{{ entityData.name }}</span>
                <span v-else class="entity-name mono">{{ neid }}</span>
                <v-chip size="x-small" variant="outlined" class="ml-2 neid-label">
                    {{ neid }}
                </v-chip>
                <v-spacer />
                <v-btn icon variant="text" size="small" @click="$emit('close')">
                    <v-icon>mdi-close</v-icon>
                </v-btn>
            </div>
            <div v-if="entityData?.aliases?.length" class="entity-aliases">
                <span class="aliases-label">Also known as:</span>
                <v-chip
                    v-for="alias in entityData.aliases"
                    :key="alias"
                    size="x-small"
                    variant="tonal"
                >
                    {{ alias }}
                </v-chip>
            </div>
        </v-card-title>

        <v-card-text v-if="loading" class="d-flex justify-center pa-8">
            <v-progress-circular indeterminate size="32" />
        </v-card-text>

        <v-card-text v-else-if="fetchError" class="error-text">
            {{ fetchError }}
        </v-card-text>

        <v-card-text v-else-if="entityData" class="entity-info-body">
            <div v-if="!groupedProperties.length" class="no-data">
                No property data available for this entity.
            </div>

            <v-table v-else dense class="properties-table">
                <thead>
                    <tr>
                        <th>Property</th>
                        <th>Value</th>
                        <th>Recorded</th>
                    </tr>
                </thead>
                <tbody>
                    <tr v-for="row in groupedProperties" :key="row.key">
                        <td class="prop-name">
                            {{ row.name }}
                            <span class="prop-pid">PID {{ row.pid }}</span>
                        </td>
                        <td class="prop-value">
                            <span
                                v-if="row.isNeid"
                                class="linked-neid"
                                @click="$emit('inspect', String(row.value))"
                            >
                                {{ row.value }}
                            </span>
                            <span v-else>{{ formatValue(row.value) }}</span>
                        </td>
                        <td class="prop-date">{{ formatDate(row.recorded_at) }}</td>
                    </tr>
                </tbody>
            </v-table>

            <div class="raw-count">
                {{ entityData.properties.length }} raw property value(s) returned
            </div>
        </v-card-text>
    </v-card>
</template>

<script setup lang="ts">
    const props = defineProps<{
        neid: string;
    }>();

    defineEmits<{
        close: [];
        inspect: [neid: string];
    }>();

    const { properties: schemaPids, refresh: refreshSchema, propertyName } = useElementalSchema();

    interface EntityApiResponse {
        neid: string;
        name: string | null;
        aliases: string[];
        properties: Array<{
            eid: string;
            pid: number;
            value: any;
            recorded_at?: string;
            attributes?: Record<string, any>;
        }>;
    }

    const entityData = ref<EntityApiResponse | null>(null);
    const loading = ref(false);
    const fetchError = ref<string | null>(null);

    interface PropertyRow {
        key: string;
        pid: number;
        name: string;
        value: any;
        recorded_at?: string;
        isNeid: boolean;
    }

    const groupedProperties = computed<PropertyRow[]>(() => {
        if (!entityData.value) return [];
        const seen = new Map<number, PropertyRow>();
        for (const v of entityData.value.properties) {
            if (seen.has(v.pid)) continue;
            const pidInfo = schemaPids.value.find((p) => p.pid === v.pid);
            const isNeid = pidInfo?.type === 'data_nindex';
            seen.set(v.pid, {
                key: `${v.pid}-${v.recorded_at}`,
                pid: v.pid,
                name: pidInfo?.name || propertyName(v.pid) || `property_${v.pid}`,
                value: v.value,
                recorded_at: v.recorded_at,
                isNeid,
            });
        }
        return Array.from(seen.values()).sort((a, b) => a.pid - b.pid);
    });

    function formatValue(value: any): string {
        if (value == null) return '—';
        if (typeof value === 'object') return JSON.stringify(value);
        return String(value);
    }

    function formatDate(d?: string): string {
        if (!d) return '—';
        return d.slice(0, 10);
    }

    async function fetchEntity(neid: string) {
        loading.value = true;
        fetchError.value = null;
        entityData.value = null;

        if (!/^\d{15,20}$/.test(neid)) {
            fetchError.value = `"${neid}" is not a valid NEID. Unable to display entity data.`;
            loading.value = false;
            return;
        }

        try {
            await refreshSchema();
            const data = await $fetch<EntityApiResponse>(`/api/entity/${neid}`);
            entityData.value = data;
        } catch (e: any) {
            fetchError.value = e?.data?.statusMessage || e.message || 'Failed to load entity data';
        } finally {
            loading.value = false;
        }
    }

    watch(
        () => props.neid,
        (neid) => {
            if (neid) fetchEntity(neid);
        },
        { immediate: true }
    );
</script>

<style scoped>
    .entity-info-card {
        margin-top: 24px;
        border-color: rgba(128, 128, 128, 0.3);
    }

    .entity-info-header {
        padding-bottom: 8px;
    }

    .entity-info-title-row {
        display: flex;
        align-items: center;
        width: 100%;
    }

    .entity-name {
        font-family: var(--font-headline);
        font-weight: 400;
        font-size: 1.1rem;
    }

    .entity-name.mono {
        font-family: var(--font-mono);
        font-size: 0.9rem;
    }

    .neid-label {
        font-family: var(--font-mono);
        font-size: 0.65rem;
    }

    .entity-aliases {
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
        align-items: center;
        margin-top: 6px;
    }

    .aliases-label {
        font-family: var(--font-mono);
        font-size: 0.65rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--lv-silver);
    }

    .entity-info-body {
        padding-top: 0;
    }

    .properties-table {
        font-size: 0.8rem;
    }

    .properties-table th {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--lv-silver);
    }

    .prop-name {
        font-weight: 500;
        white-space: nowrap;
    }

    .prop-pid {
        font-family: var(--font-mono);
        font-size: 0.65rem;
        color: var(--lv-silver);
        margin-left: 6px;
    }

    .prop-value {
        font-family: var(--font-mono);
        font-size: 0.75rem;
        word-break: break-all;
        max-width: 400px;
    }

    .linked-neid {
        color: rgb(var(--v-theme-primary));
        cursor: pointer;
        text-decoration: underline;
        text-decoration-style: dotted;
    }

    .linked-neid:hover {
        text-decoration-style: solid;
    }

    .prop-date {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        color: var(--lv-silver);
        white-space: nowrap;
    }

    .no-data {
        color: var(--lv-silver);
        font-style: italic;
        padding: 16px 0;
    }

    .error-text {
        color: rgb(var(--v-theme-error));
    }

    .raw-count {
        font-family: var(--font-mono);
        font-size: 0.65rem;
        color: var(--lv-silver);
        text-align: right;
        padding-top: 8px;
    }
</style>
