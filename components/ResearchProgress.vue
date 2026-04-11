<template>
    <div class="research-progress">
        <h2 class="section-title">Researching...</h2>
        <v-progress-linear indeterminate color="primary" class="mb-4" />

        <div class="steps-list">
            <div v-for="step in steps" :key="step.id" class="step-item">
                <v-icon size="small" :color="toolIconColor(step.tool)" class="step-icon">
                    {{ toolIcon(step.tool) }}
                </v-icon>
                <span class="step-label">{{ step.label }}</span>
                <span class="step-time">{{ formatTime(step.timestamp) }}</span>
            </div>
        </div>

        <div v-if="steps.length === 0" class="steps-empty">
            <v-progress-circular indeterminate size="20" width="2" class="mr-2" />
            <span>Agent is planning research approach...</span>
        </div>
    </div>
</template>

<script setup lang="ts">
    import type { ResearchStep } from '~/composables/useThesisResearch';

    defineProps<{
        steps: ResearchStep[];
    }>();

    const TOOL_ICONS: Record<string, string> = {
        get_news: 'mdi-newspaper-variant-outline',
        get_stock_prices: 'mdi-chart-line',
        get_filings: 'mdi-file-document-outline',
        get_relationships: 'mdi-graph-outline',
        get_events: 'mdi-calendar-star',
        get_macro: 'mdi-trending-up',
        get_entity_properties: 'mdi-database-search-outline',
        finalize_research: 'mdi-check-circle-outline',
    };

    const TOOL_COLORS: Record<string, string> = {
        get_news: 'info',
        get_stock_prices: 'success',
        get_filings: 'warning',
        get_relationships: 'primary',
        get_events: 'warning',
        get_macro: 'info',
        get_entity_properties: 'default',
        finalize_research: 'success',
    };

    function toolIcon(tool: string): string {
        return TOOL_ICONS[tool] || 'mdi-cog-outline';
    }

    function toolIconColor(tool: string): string {
        return TOOL_COLORS[tool] || 'default';
    }

    function formatTime(timestamp: number): string {
        return new Date(timestamp).toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
        });
    }
</script>

<style scoped>
    .research-progress {
        max-width: 720px;
        width: 100%;
        margin: 0 auto;
    }

    .section-title {
        font-family: var(--font-headline);
        font-weight: 400;
        font-size: 1.3rem;
        letter-spacing: 0.02em;
        margin-bottom: 12px;
    }

    .steps-list {
        display: flex;
        flex-direction: column;
        gap: 8px;
    }

    .step-item {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 8px 12px;
        background: var(--lv-surface);
        border-radius: 8px;
    }

    .step-icon {
        flex-shrink: 0;
    }

    .step-label {
        flex: 1;
        font-size: 0.9rem;
    }

    .step-time {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        color: var(--lv-silver);
        flex-shrink: 0;
    }

    .steps-empty {
        display: flex;
        align-items: center;
        color: var(--lv-silver);
        font-size: 0.9rem;
        padding: 12px 0;
    }
</style>
