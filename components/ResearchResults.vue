<template>
    <div class="research-results">
        <div class="results-header">
            <h2 class="section-title">Research Results</h2>
            <div v-if="results.entities_examined.length" class="entities-examined">
                <span class="entities-label">Entities examined:</span>
                <v-chip
                    v-for="entity in results.entities_examined"
                    :key="entity"
                    size="small"
                    variant="tonal"
                >
                    {{ entity }}
                </v-chip>
            </div>
        </div>

        <v-row>
            <v-col cols="12" md="6">
                <div class="signal-column supporting">
                    <div class="column-header">
                        <v-icon color="success" class="mr-2">mdi-thumb-up-outline</v-icon>
                        <span class="column-title">Supporting Signals</span>
                        <v-chip size="x-small" color="success" variant="tonal">
                            {{ results.supporting.evidence.length }}
                        </v-chip>
                    </div>

                    <div v-if="results.supporting.evidence.length === 0" class="no-signals">
                        No supporting evidence found.
                    </div>
                    <SignalCard
                        v-for="(item, idx) in results.supporting.evidence"
                        :key="'s-' + idx"
                        :evidence="item"
                    />

                    <v-card
                        v-if="results.supporting.analysis"
                        variant="tonal"
                        color="success"
                        class="analysis-card"
                    >
                        <v-card-title class="analysis-title">
                            <v-icon size="small" class="mr-1">mdi-text-box-outline</v-icon>
                            Analysis
                        </v-card-title>
                        <v-card-text class="analysis-text">
                            {{ results.supporting.analysis }}
                        </v-card-text>
                    </v-card>
                </div>
            </v-col>

            <v-col cols="12" md="6">
                <div class="signal-column contradicting">
                    <div class="column-header">
                        <v-icon color="warning" class="mr-2">mdi-thumb-down-outline</v-icon>
                        <span class="column-title">Contradicting Signals</span>
                        <v-chip size="x-small" color="warning" variant="tonal">
                            {{ results.contradicting.evidence.length }}
                        </v-chip>
                    </div>

                    <div v-if="results.contradicting.evidence.length === 0" class="no-signals">
                        No contradicting evidence found.
                    </div>
                    <SignalCard
                        v-for="(item, idx) in results.contradicting.evidence"
                        :key="'c-' + idx"
                        :evidence="item"
                    />

                    <v-card
                        v-if="results.contradicting.analysis"
                        variant="tonal"
                        color="warning"
                        class="analysis-card"
                    >
                        <v-card-title class="analysis-title">
                            <v-icon size="small" class="mr-1">mdi-text-box-outline</v-icon>
                            Analysis
                        </v-card-title>
                        <v-card-text class="analysis-text">
                            {{ results.contradicting.analysis }}
                        </v-card-text>
                    </v-card>
                </div>
            </v-col>
        </v-row>

        <v-card v-if="results.limitations" variant="outlined" class="limitations-card mt-4">
            <v-card-title class="limitations-title">
                <v-icon size="small" class="mr-1">mdi-alert-circle-outline</v-icon>
                Limitations & Caveats
            </v-card-title>
            <v-card-text class="limitations-text">
                {{ results.limitations }}
            </v-card-text>
        </v-card>
    </div>
</template>

<script setup lang="ts">
    import type { ThesisResults } from '~/composables/useThesisResearch';

    defineProps<{
        results: ThesisResults;
    }>();
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

    .column-header {
        display: flex;
        align-items: center;
        gap: 4px;
        margin-bottom: 12px;
    }

    .column-title {
        font-family: var(--font-headline);
        font-weight: 400;
        font-size: 1rem;
        letter-spacing: 0.02em;
        flex: 1;
    }

    .no-signals {
        color: var(--lv-silver);
        font-size: 0.9rem;
        font-style: italic;
        padding: 12px 0;
    }

    .analysis-card {
        margin-top: 12px;
    }

    .analysis-title {
        font-family: var(--font-headline);
        font-size: 0.85rem;
        padding-bottom: 0;
    }

    .analysis-text {
        font-size: 0.9rem;
        line-height: 1.6;
    }

    .limitations-card {
        border-color: var(--lv-silver);
    }

    .limitations-title {
        font-family: var(--font-headline);
        font-size: 0.85rem;
        color: var(--lv-silver);
        padding-bottom: 0;
    }

    .limitations-text {
        font-size: 0.85rem;
        color: var(--lv-silver);
        line-height: 1.5;
    }
</style>
