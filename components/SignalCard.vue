<template>
    <v-card variant="outlined" class="signal-card">
        <v-card-text>
            <div class="signal-header">
                <v-chip size="x-small" variant="tonal" :color="sourceColor" class="source-chip">
                    <v-icon start size="x-small">{{ sourceIcon }}</v-icon>
                    {{ evidence.source }}
                </v-chip>
                <span v-if="evidence.entity" class="signal-entity">{{ evidence.entity }}</span>
                <span v-if="evidence.date" class="signal-date">{{ evidence.date }}</span>
            </div>
            <div class="signal-title">{{ evidence.title }}</div>
            <div class="signal-detail">{{ evidence.detail }}</div>

            <div
                v-if="evidence.neid || evidence.source_url || evidence.tool_used"
                class="signal-provenance"
            >
                <v-chip
                    v-if="evidence.source_url"
                    size="x-small"
                    variant="outlined"
                    :href="evidence.source_url"
                    target="_blank"
                    rel="noopener"
                    class="provenance-chip"
                >
                    <v-icon start size="x-small">mdi-open-in-new</v-icon>
                    Source
                </v-chip>
                <v-chip
                    v-if="evidence.neid"
                    size="x-small"
                    variant="outlined"
                    class="provenance-chip neid-chip clickable"
                    @click="$emit('inspect', evidence.neid!)"
                >
                    <v-icon start size="x-small">mdi-identifier</v-icon>
                    {{ evidence.neid }}
                </v-chip>
                <v-chip
                    v-if="evidence.tool_used"
                    size="x-small"
                    variant="outlined"
                    class="provenance-chip tool-chip"
                >
                    <v-icon start size="x-small">mdi-wrench-outline</v-icon>
                    {{ toolDisplayName(evidence.tool_used) }}
                </v-chip>
            </div>
        </v-card-text>
    </v-card>
</template>

<script setup lang="ts">
    import type { EvidenceItem } from '~/composables/useThesisResearch';

    const props = defineProps<{
        evidence: EvidenceItem;
    }>();

    defineEmits<{
        inspect: [neid: string];
    }>();

    const SOURCE_CONFIG: Record<string, { icon: string; color: string }> = {
        news: { icon: 'mdi-newspaper-variant-outline', color: 'info' },
        filing: { icon: 'mdi-file-document-outline', color: 'warning' },
        stock: { icon: 'mdi-chart-line', color: 'success' },
        event: { icon: 'mdi-calendar-star', color: 'warning' },
        relationship: { icon: 'mdi-graph-outline', color: 'primary' },
        macro: { icon: 'mdi-trending-up', color: 'info' },
    };

    const TOOL_NAMES: Record<string, string> = {
        get_entity_news: 'News',
        get_stock_prices: 'Stock Prices',
        get_entity_filings: 'Filings',
        get_entity_relationships: 'Relationships',
        get_entity_events: 'Events',
        get_macro_data: 'Macro Data',
        get_schema: 'Schema',
        lookup_entity: 'Entity Lookup',
    };

    function toolDisplayName(tool: string): string {
        return TOOL_NAMES[tool] || tool;
    }

    const sourceIcon = computed(
        () => SOURCE_CONFIG[props.evidence.source]?.icon || 'mdi-information-outline'
    );
    const sourceColor = computed(() => SOURCE_CONFIG[props.evidence.source]?.color || 'default');
</script>

<style scoped>
    .signal-card {
        margin-bottom: 8px;
    }

    .signal-header {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 8px;
        flex-wrap: wrap;
    }

    .signal-entity {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        color: var(--lv-silver);
    }

    .signal-date {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        color: var(--lv-silver);
        margin-left: auto;
    }

    .signal-title {
        font-weight: 500;
        font-size: 0.95rem;
        margin-bottom: 4px;
    }

    .signal-detail {
        font-size: 0.85rem;
        color: var(--lv-silver);
        line-height: 1.5;
    }

    .signal-provenance {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin-top: 10px;
        padding-top: 8px;
        border-top: 1px solid rgba(128, 128, 128, 0.15);
    }

    .provenance-chip {
        font-family: var(--font-mono);
        font-size: 0.65rem;
        letter-spacing: 0.02em;
    }

    .neid-chip {
        max-width: 180px;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .clickable {
        cursor: pointer;
    }

    .clickable:hover {
        border-color: rgb(var(--v-theme-primary));
        color: rgb(var(--v-theme-primary));
    }

    .tool-chip {
        color: var(--lv-silver);
    }
</style>
