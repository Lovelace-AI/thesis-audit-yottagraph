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
        </v-card-text>
    </v-card>
</template>

<script setup lang="ts">
    import type { EvidenceItem } from '~/composables/useThesisResearch';

    const props = defineProps<{
        evidence: EvidenceItem;
    }>();

    const SOURCE_CONFIG: Record<string, { icon: string; color: string }> = {
        news: { icon: 'mdi-newspaper-variant-outline', color: 'info' },
        filing: { icon: 'mdi-file-document-outline', color: 'warning' },
        stock: { icon: 'mdi-chart-line', color: 'success' },
        event: { icon: 'mdi-calendar-star', color: 'warning' },
        relationship: { icon: 'mdi-graph-outline', color: 'primary' },
        macro: { icon: 'mdi-trending-up', color: 'info' },
    };

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
</style>
