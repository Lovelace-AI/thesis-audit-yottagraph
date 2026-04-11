<template>
    <div class="research-progress">
        <h2 class="section-title">Researching...</h2>
        <v-progress-linear indeterminate color="primary" class="mb-4" />

        <div class="iterations-list">
            <div v-for="iter in iterations" :key="iter.id" class="iteration-block">
                <div class="iteration-header" @click="toggleIteration(iter.id)">
                    <v-icon size="small" class="expand-icon">
                        {{
                            expandedIterations[iter.id] === false
                                ? 'mdi-chevron-right'
                                : 'mdi-chevron-down'
                        }}
                    </v-icon>
                    <v-icon
                        size="small"
                        :color="iter.status === 'done' ? 'success' : 'primary'"
                        class="mr-1"
                    >
                        {{
                            iter.status === 'done'
                                ? 'mdi-check-circle-outline'
                                : 'mdi-loading mdi-spin'
                        }}
                    </v-icon>
                    <span class="iteration-title">Iteration {{ iter.iteration }}</span>
                    <span class="iteration-time">{{ formatTime(iter.timestamp) }}</span>
                </div>

                <div v-if="iter.reasoning" class="iteration-reasoning">{{ iter.reasoning }}</div>

                <div v-if="expandedIterations[iter.id] !== false" class="calls-list">
                    <div v-for="call in iter.calls" :key="call.id" class="call-item">
                        <v-icon size="small" :color="toolIconColor(call.type)" class="call-icon">
                            {{ toolIcon(call.type) }}
                        </v-icon>
                        <span class="call-label">{{ call.label }}</span>
                        <span v-if="call.summary" class="call-summary">{{ call.summary }}</span>
                    </div>

                    <div
                        v-if="iter.status === 'planning' && iter.calls.length === 0"
                        class="call-item call-pending"
                    >
                        <v-progress-circular indeterminate size="16" width="2" class="mr-2" />
                        <span>Planning next steps...</span>
                    </div>
                </div>
            </div>
        </div>

        <div v-if="iterations.length === 0" class="steps-empty">
            <v-progress-circular indeterminate size="20" width="2" class="mr-2" />
            <span>Agent is starting research...</span>
        </div>
    </div>
</template>

<script setup lang="ts">
    import { reactive } from 'vue';
    import type { ResearchIteration } from '~/composables/useThesisResearch';

    defineProps<{
        iterations: ResearchIteration[];
    }>();

    const expandedIterations = reactive<Record<string, boolean>>({});

    function toggleIteration(id: string) {
        expandedIterations[id] = expandedIterations[id] === false;
    }

    const TOOL_ICONS: Record<string, string> = {
        get_news: 'mdi-newspaper-variant-outline',
        get_stock_prices: 'mdi-chart-line',
        get_filings: 'mdi-file-document-outline',
        get_relationships: 'mdi-graph-outline',
        get_events: 'mdi-calendar-star',
        get_entity_properties: 'mdi-database-search-outline',
    };

    const TOOL_COLORS: Record<string, string> = {
        get_news: 'info',
        get_stock_prices: 'success',
        get_filings: 'warning',
        get_relationships: 'primary',
        get_events: 'warning',
        get_entity_properties: 'default',
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

    .iterations-list {
        display: flex;
        flex-direction: column;
        gap: 12px;
    }

    .iteration-block {
        background: var(--lv-surface);
        border-radius: 10px;
        overflow: hidden;
    }

    .iteration-header {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 10px 14px;
        cursor: pointer;
        user-select: none;
    }

    .iteration-header:hover {
        background: rgba(var(--v-theme-on-surface), 0.04);
    }

    .expand-icon {
        flex-shrink: 0;
        opacity: 0.5;
    }

    .iteration-title {
        font-weight: 500;
        font-size: 0.95rem;
        flex: 1;
    }

    .iteration-time {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        color: var(--lv-silver);
        flex-shrink: 0;
    }

    .iteration-reasoning {
        padding: 0 14px 8px 46px;
        font-size: 0.82rem;
        color: var(--lv-silver);
        font-style: italic;
    }

    .calls-list {
        display: flex;
        flex-direction: column;
        gap: 4px;
        padding: 0 14px 10px 46px;
    }

    .call-item {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 6px 10px;
        border-radius: 6px;
        background: rgba(var(--v-theme-on-surface), 0.03);
    }

    .call-icon {
        flex-shrink: 0;
    }

    .call-label {
        font-size: 0.85rem;
        flex-shrink: 0;
    }

    .call-summary {
        font-size: 0.8rem;
        color: var(--lv-silver);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        flex: 1;
        min-width: 0;
    }

    .call-pending {
        color: var(--lv-silver);
        font-size: 0.85rem;
    }

    .steps-empty {
        display: flex;
        align-items: center;
        color: var(--lv-silver);
        font-size: 0.9rem;
        padding: 12px 0;
    }
</style>
