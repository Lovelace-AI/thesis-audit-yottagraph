<template>
    <div class="research-results">
        <div class="results-header">
            <h2 class="section-title">Research Report</h2>
            <div v-if="entityNames.length" class="entities-examined">
                <span class="entities-label">Entities examined:</span>
                <v-chip
                    v-for="entity in entityNames"
                    :key="entity.name"
                    size="small"
                    variant="tonal"
                    :class="{ 'entity-link': !!entity.neid }"
                    @click="entity.neid && $emit('inspect', entity.neid)"
                >
                    {{ entity.name }}
                </v-chip>
            </div>
        </div>

        <!-- Analysis columns -->
        <v-row>
            <v-col cols="12" md="6">
                <v-card variant="tonal" color="success" class="analysis-card">
                    <v-card-title class="analysis-title">
                        <v-icon size="small" class="mr-2">mdi-thumb-up-outline</v-icon>
                        Supporting Argument
                    </v-card-title>
                    <v-card-text class="analysis-text">
                        {{ report.supporting_argument || 'No supporting argument provided.' }}
                    </v-card-text>
                </v-card>
            </v-col>

            <v-col cols="12" md="6">
                <v-card variant="tonal" color="warning" class="analysis-card">
                    <v-card-title class="analysis-title">
                        <v-icon size="small" class="mr-2">mdi-thumb-down-outline</v-icon>
                        Contradicting Argument
                    </v-card-title>
                    <v-card-text class="analysis-text">
                        {{ report.contradicting_argument || 'No contradicting argument provided.' }}
                    </v-card-text>
                </v-card>
            </v-col>
        </v-row>

        <!-- Final analysis -->
        <v-card v-if="report.final_analysis" variant="outlined" class="final-analysis-card mt-4">
            <v-card-title class="final-analysis-title">
                <v-icon size="small" class="mr-2">mdi-scale-balance</v-icon>
                Final Analysis
            </v-card-title>
            <v-card-text class="analysis-text">
                {{ report.final_analysis }}
            </v-card-text>
        </v-card>

        <!-- Research iterations log -->
        <v-expansion-panels v-if="iterations?.length" variant="accordion" class="mt-4">
            <v-expansion-panel>
                <v-expansion-panel-title class="provenance-title">
                    <v-icon size="small" class="mr-2">mdi-repeat</v-icon>
                    Research Iterations
                    <v-chip size="x-small" variant="tonal" class="ml-2">
                        {{ iterations.length }} iterations
                    </v-chip>
                </v-expansion-panel-title>
                <v-expansion-panel-text>
                    <div v-for="iter in iterations" :key="iter.id" class="provenance-step">
                        <div class="provenance-step-header">
                            <v-chip size="x-small" variant="tonal" color="primary">
                                Iteration {{ iter.iteration }}
                            </v-chip>
                            <span v-if="iter.reasoning" class="provenance-step-args">
                                {{ iter.reasoning }}
                            </span>
                        </div>
                        <div v-for="call in iter.calls" :key="call.id" class="iteration-call">
                            <v-icon size="x-small" class="mr-1">{{
                                TOOL_ICONS[call.type] || 'mdi-cog-outline'
                            }}</v-icon>
                            <span class="call-type-label">{{ call.label }}</span>
                            <span class="call-summary-text">{{ call.summary }}</span>
                        </div>
                    </div>
                </v-expansion-panel-text>
            </v-expansion-panel>
        </v-expansion-panels>

        <!-- Research data — each API call individually expandable with raw JSON -->
        <div v-if="report.calls?.length" class="mt-4">
            <div class="provenance-section-header">
                <v-icon size="small" class="mr-2">mdi-database-outline</v-icon>
                <span class="provenance-title-text">Research Data</span>
                <v-chip size="x-small" variant="tonal" class="ml-2">
                    {{ report.calls.length }} API calls
                </v-chip>
            </div>
            <v-expansion-panels variant="accordion" class="mt-2">
                <v-expansion-panel v-for="call in report.calls" :key="call.id">
                    <v-expansion-panel-title class="call-panel-title">
                        <v-chip size="x-small" variant="tonal" color="primary" class="mr-2">
                            {{ call.type }}
                        </v-chip>
                        <span class="call-panel-params">{{ formatCallParams(call) }}</span>
                        <v-chip
                            size="x-small"
                            :color="call.status === 'ok' ? 'success' : 'error'"
                            variant="tonal"
                            class="ml-auto"
                        >
                            {{ call.status }}
                        </v-chip>
                    </v-expansion-panel-title>
                    <v-expansion-panel-text>
                        <pre class="provenance-step-response">{{ formatCallJSON(call) }}</pre>
                    </v-expansion-panel-text>
                </v-expansion-panel>
            </v-expansion-panels>
        </div>

        <div class="results-actions">
            <v-btn variant="outlined" @click="$emit('edit')">
                <v-icon start>mdi-pencil-outline</v-icon>
                Edit Thesis
            </v-btn>
            <v-btn variant="outlined" @click="$emit('reset')">
                <v-icon start>mdi-plus</v-icon>
                Propose New Thesis
            </v-btn>
        </div>
    </div>
</template>

<script setup lang="ts">
    import type { ReportResult, ResearchIteration } from '~/composables/useThesisResearch';

    const props = defineProps<{
        report: ReportResult;
        iterations?: ResearchIteration[];
    }>();

    defineEmits<{
        edit: [];
        reset: [];
        inspect: [neid: string];
    }>();

    const TOOL_ICONS: Record<string, string> = {
        get_news: 'mdi-newspaper-variant-outline',
        get_stock_prices: 'mdi-chart-line',
        get_filings: 'mdi-file-document-outline',
        get_relationships: 'mdi-graph-outline',
        get_events: 'mdi-calendar-star',
        get_entity_properties: 'mdi-database-search-outline',
    };

    const entityNames = computed(() => {
        const entities = props.report.query?.entities ?? [];
        return entities
            .filter((e) => e.status === 'resolved')
            .map((e) => ({
                name: e.name || e.mentioned_as,
                neid: e.neid,
            }));
    });

    function formatCallJSON(call: any): string {
        const syw = props.report.show_your_work?.[call.id];
        const payload = syw ?? call.result ?? call;
        try {
            const obj = typeof payload === 'string' ? JSON.parse(payload) : payload;
            return JSON.stringify(obj, null, 2);
        } catch {
            return typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2);
        }
    }

    function formatCallParams(call: any): string {
        const params = call.params || {};
        const entries = Object.entries(params);
        if (entries.length === 0) return '';
        return entries.map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(', ');
    }
</script>

<style scoped>
    .research-results {
        width: 100%;
    }

    .results-header {
        margin-bottom: 20px;
    }

    .section-title {
        font-family: var(--font-headline);
        font-weight: 400;
        font-size: 1.3rem;
        letter-spacing: 0.02em;
        margin-bottom: 8px;
    }

    .entities-examined {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        align-items: center;
    }

    .entities-label {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--lv-silver);
    }

    .entity-link {
        cursor: pointer;
    }

    .entity-link:hover {
        border-color: rgb(var(--v-theme-primary));
    }

    .analysis-card {
        height: 100%;
    }

    .analysis-title {
        font-family: var(--font-headline);
        font-size: 0.95rem;
        padding-bottom: 0;
    }

    .analysis-text {
        font-size: 0.9rem;
        line-height: 1.7;
        white-space: pre-wrap;
    }

    .final-analysis-card {
        border-color: rgba(128, 128, 128, 0.3);
    }

    .final-analysis-title {
        font-family: var(--font-headline);
        font-size: 0.95rem;
        padding-bottom: 0;
    }

    .provenance-title {
        font-family: var(--font-mono);
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    .provenance-section-header {
        display: flex;
        align-items: center;
        padding: 8px 0;
    }

    .provenance-title-text {
        font-family: var(--font-mono);
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    .call-panel-title {
        font-family: var(--font-mono);
        font-size: 0.75rem;
    }

    .call-panel-params {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        color: var(--lv-silver);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        flex: 1;
        min-width: 0;
    }

    .provenance-step {
        padding: 8px 0;
        border-bottom: 1px solid rgba(128, 128, 128, 0.1);
    }

    .provenance-step:last-child {
        border-bottom: none;
    }

    .provenance-step-header {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 4px;
    }

    .provenance-step-args {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        color: var(--lv-silver);
    }

    .provenance-step-response {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        line-height: 1.4;
        color: var(--lv-silver);
        background: rgba(128, 128, 128, 0.05);
        border-radius: 4px;
        padding: 8px;
        margin-top: 4px;
        white-space: pre-wrap;
        word-break: break-word;
        max-height: 60vh;
        overflow-y: auto;
    }

    .iteration-call {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 4px 0 4px 16px;
        font-size: 0.75rem;
    }

    .call-type-label {
        flex-shrink: 0;
    }

    .call-summary-text {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        color: var(--lv-silver);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        flex: 1;
        min-width: 0;
    }

    .results-actions {
        display: flex;
        justify-content: center;
        gap: 12px;
        margin-top: 32px;
        padding-top: 24px;
        border-top: 1px solid rgba(128, 128, 128, 0.2);
    }
</style>
