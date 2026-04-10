<template>
    <div class="thesis-input">
        <div class="thesis-header">
            <h1 class="thesis-title">Thesis Audit</h1>
            <p class="thesis-subtitle">
                Propose a financial theory. We'll research it against the yottagraph and report what
                supports or contradicts it.
            </p>
        </div>

        <v-textarea
            v-model="thesisText"
            variant="outlined"
            color="primary"
            rows="4"
            auto-grow
            placeholder='e.g. "When Netflix stock goes up, its competitors&apos; stocks tend to go down in the following months"'
            :disabled="disabled"
            class="thesis-field"
            @keydown.meta.enter="handleSubmit"
            @keydown.ctrl.enter="handleSubmit"
        />

        <div class="thesis-actions">
            <v-btn
                color="primary"
                size="large"
                :disabled="!thesisText.trim() || disabled"
                :loading="disabled"
                @click="handleSubmit"
            >
                Research This Thesis
            </v-btn>
        </div>

        <div v-if="!disabled" class="thesis-examples">
            <span class="examples-label">Try an example:</span>
            <div class="examples-chips">
                <v-chip
                    v-for="example in examples"
                    :key="example"
                    variant="outlined"
                    size="small"
                    class="example-chip"
                    @click="thesisText = example"
                >
                    {{ example }}
                </v-chip>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
    const thesisText = defineModel<string>('modelValue', { default: '' });

    defineProps<{
        disabled?: boolean;
    }>();

    const emit = defineEmits<{
        submit: [thesis: string];
    }>();

    const EXAMPLE_POOL = [
        'When Netflix stock rises, Disney and Paramount stocks tend to fall within 3 months',
        'Banks with high exposure to commercial real estate are more likely to face regulatory action',
        'Companies that announce major layoffs see their stock price increase in the short term',
        'Rising federal funds rate correlates with declining tech sector valuations',
        'When oil prices spike, airline stocks drop disproportionately compared to the broader market',
        'Companies that acquire competitors tend to underperform the S&P 500 for the following year',
        'Insider selling at tech companies precedes stock price declines within 60 days',
        'Regional banks outperform large banks when the yield curve steepens',
        'Pharmaceutical companies that receive FDA fast-track designation see their stock rise',
        'When the VIX spikes above 30, buying the S&P 500 produces above-average 6-month returns',
        'Defense stocks rally when geopolitical tensions rise in the Middle East',
        'Companies with high ESG ratings outperform their sector peers during market downturns',
        'Retail stocks decline in months following consumer confidence drops below 80',
        'Semiconductor stocks lead the broader tech sector by roughly one quarter',
        'Gold prices and real interest rates are inversely correlated over multi-year periods',
        'FAANG stocks move together more during periods of high market volatility',
        'IPOs from unprofitable companies underperform the market in their first two years',
        'Housing starts predict home-improvement retailer earnings two quarters ahead',
        "Companies that raise dividends consistently outperform those that don't over 10-year periods",
        'Credit card delinquency rates predict consumer discretionary sector performance',
        'When copper prices rise, emerging market equities tend to outperform developed markets',
        'Activist investor involvement correlates with short-term stock price gains but long-term underperformance',
        'Bank stocks fall when FDIC enforcement actions increase quarter-over-quarter',
        "Electric vehicle makers' stock prices are correlated with lithium commodity prices",
    ];

    function pickRandom<T>(pool: T[], count: number): T[] {
        const shuffled = [...pool].sort(() => Math.random() - 0.5);
        return shuffled.slice(0, count);
    }

    const examples = pickRandom(EXAMPLE_POOL, 4);

    function handleSubmit() {
        if (thesisText.value.trim()) {
            emit('submit', thesisText.value.trim());
        }
    }
</script>

<style scoped>
    .thesis-input {
        max-width: 720px;
        width: 100%;
        margin: 0 auto;
    }

    .thesis-header {
        text-align: center;
        margin-bottom: 32px;
    }

    .thesis-title {
        font-family: var(--font-headline);
        font-weight: 400;
        font-size: 2.2rem;
        letter-spacing: 0.02em;
        margin-bottom: 8px;
    }

    .thesis-subtitle {
        color: var(--lv-silver);
        font-size: 1rem;
        line-height: 1.5;
    }

    .thesis-field {
        margin-bottom: 8px;
    }

    .thesis-actions {
        display: flex;
        justify-content: center;
        margin-bottom: 24px;
    }

    .thesis-examples {
        display: flex;
        flex-direction: column;
        gap: 8px;
    }

    .examples-label {
        font-family: var(--font-mono);
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--lv-silver);
    }

    .examples-chips {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
    }

    .example-chip {
        cursor: pointer;
    }
</style>
