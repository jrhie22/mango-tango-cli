import plotly.express as px
import plotly.graph_objects as go
import polars as pl
from shiny import reactive, render, ui
from shinywidgets import output_widget, render_widget

from ..ngram_stats.interface import (
    COL_NGRAM_DISTINCT_POSTER_COUNT,
    COL_NGRAM_TOTAL_REPS,
    COL_NGRAM_WORDS,
)
from ..ngrams_base.interface import (
    COL_AUTHOR_ID,
    COL_MESSAGE_TEXT,
    COL_MESSAGE_TIMESTAMP,
    COL_NGRAM_ID,
    COL_NGRAM_LENGTH,
)

MANGO_DARK_GREEN = "#609949"
CLICKED_COLOR = "red"
data_stats = None  # secondary output
data_full = None  # primary output
ngram_choices_dict = {}


def _set_global_state_vars(df_stats: pl.DataFrame, df_full: pl.DataFrame):
    global data_stats, data_full

    data_stats = df_stats
    data_full = df_full


def plot_scatter(data):
    import numpy as np

    # Add jitter to x-axis to separate overlapping points
    # Since we're using log scale, we need to be careful with the jitter amount
    rng = np.random.default_rng(seed=42)  # For reproducible jitter

    # Create jitter as a small multiplier (5% of the log value)
    jitter_factor = 0.05
    data = data.with_columns(
        (
            pl.col(COL_NGRAM_TOTAL_REPS)
            * (1 + rng.uniform(-jitter_factor, jitter_factor, len(data)))
        ).alias("total_reps_jittered")
    )

    n_gram_categories = data.select(pl.col("n").unique().sort()).to_series()

    fig = px.scatter(
        data_frame=data,
        x="distinct_posters",
        y="total_reps_jittered",
        log_y=True,
        log_x=True,
        custom_data=[COL_NGRAM_WORDS, COL_NGRAM_ID, COL_NGRAM_TOTAL_REPS],
        color="n",
        category_orders={"n": n_gram_categories},
    )

    fig.update_traces(
        marker=dict(size=11, opacity=0.7, line=dict(width=0.5, color="white")),
        hovertemplate="<br>".join(
            [
                "<b>Ngram:</b> %{customdata[0]}",
                "<b>Frequency:</b> %{customdata[2]}",
                "<b>Nr. unique posters:</b> %{x}",
            ]
        ),
    )

    # Customize the plot
    fig.update_layout(
        title_text="Repeated phrases and accounts",
        yaxis_title="N-gram frequency",
        xaxis_title="Nr. unique posters",
        hovermode="closest",
        legend=dict(title="N-gram length"),
        template="plotly_white",
    )

    return fig


# helper to clear markers from figure widget
def _remove_markers(figure_widget: go.FigureWidget) -> go.FigureWidget:
    traces_to_remove = []
    for i, existing_trace in enumerate(figure_widget.data):
        try:
            if (
                hasattr(existing_trace, "marker")
                and hasattr(existing_trace.marker, "color")
                and str(existing_trace.marker.color) == CLICKED_COLOR
            ):
                traces_to_remove.append(i)
        except Exception:
            continue

    # Remove old markers in reverse order
    for i in reversed(traces_to_remove):
        try:
            figure_widget.data = figure_widget.data[:i] + figure_widget.data[i + 1 :]
        except Exception:
            continue

    return figure_widget


def _get_app_layout(ngram_choices_dict: dict):
    app_layout = [
        ui.card(
            ui.card_header("N-gram statistics"),
            ui.input_selectize(
                id="ngram_selector",
                label="Included n-grams:",
                choices=ngram_choices_dict,
                selected=list(ngram_choices_dict.keys()),
                multiple=True,
            ),
            output_widget("scatter_plot", height="400px"),
        ),
        ui.card(
            ui.card_header("Search & Filter"),
            ui.row(
                ui.column(
                    6,
                    ui.input_text(
                        id="user_id_search",
                        label="Search by User ID:",
                        placeholder="Enter user ID to search",
                    ),
                ),
                ui.column(
                    6,
                    ui.input_text(
                        id="ngram_content_search",
                        label="Search N-gram Content:",
                        placeholder="Enter keywords to search",
                    ),
                ),
            ),
        ),
        ui.card(
            ui.card_header("Data viewer"),
            ui.output_text(id="data_info"),
            ui.div(
                ui.input_action_button(
                    id="reset_button",
                    label="Reset selection (show summary)",
                    fill=False,
                ),
                style="display: inline-block; margin-bottom: 10px;",
            ),
            ui.output_data_frame("data_viewer"),
        ),
    ]

    return app_layout


def server(input, output, sessions):
    @reactive.calc
    def get_data():
        global data_stats

        return data_stats.filter(pl.col("n").is_in(input.ngram_selector()))

    @reactive.calc
    def get_search_filtered_data():
        """Filter data based on user ID and n-gram content search"""
        global data_full
        
        # Get search values once to avoid multiple reactive calls
        user_search = input.user_id_search() or ""
        content_search = input.ngram_content_search() or ""
        
        # If no search terms, return original data
        if not user_search.strip() and not content_search.strip():
            return data_full
        
        filtered_data = data_full
        
        # Filter by user ID if provided
        if user_search.strip():
            filtered_data = filtered_data.filter(
                pl.col(COL_AUTHOR_ID).str.contains(user_search.strip(), literal=False)
            )
        
        # Filter by n-gram content if provided
        if content_search.strip():
            filtered_data = filtered_data.filter(
                pl.col(COL_NGRAM_WORDS).str.contains(content_search.strip(), literal=False)
            )
        
        return filtered_data

    @reactive.calc
    def get_search_filtered_stats():
        """Get n-gram statistics for search-filtered data"""
        search_data = get_search_filtered_data()
        
        if search_data.is_empty():
            return data_stats.head(0)  # Return empty dataframe with same schema
        
        # Get unique n-gram IDs from filtered data
        filtered_ngram_ids = search_data.select(COL_NGRAM_ID).unique()
        
        # Filter stats data to only include n-grams that appear in search results
        filtered_stats = data_stats.filter(
            pl.col(COL_NGRAM_ID).is_in(filtered_ngram_ids[COL_NGRAM_ID])
        )
        
        return filtered_stats

    # Store the figure widget reference
    current_figure_widget = reactive.value(None)

    @reactive.effect
    def handle_reset_button():
        # if reset button is clicked
        # disable it and clear traces
        if input.reset_button():
            # Reset the click data
            click_data.set(None)
            # Reset the scatter plot by removing any red markers
            fig_widget = current_figure_widget()
            if fig_widget is not None:
                try:
                    # Remove red marker traces
                    fig_widget = _remove_markers(figure_widget=fig_widget)
                except Exception:
                    pass



    @reactive.calc
    def get_top_n_data():
        global data_stats

        data_top_n = (
            data_stats.select(
                [
                    COL_NGRAM_WORDS,
                    COL_NGRAM_TOTAL_REPS,
                    COL_NGRAM_DISTINCT_POSTER_COUNT,
                    COL_NGRAM_LENGTH,
                ]
            )
            .sort(
                pl.col(COL_NGRAM_TOTAL_REPS),
                pl.col(COL_NGRAM_DISTINCT_POSTER_COUNT),
                descending=[True, True],
            )
            .rename(
                {
                    COL_NGRAM_WORDS: "N-gram content",
                    COL_NGRAM_TOTAL_REPS: "Nr. total repetitions",
                    COL_NGRAM_DISTINCT_POSTER_COUNT: "Nr. unique posters",
                    COL_NGRAM_LENGTH: "N-gram length",
                }
            )
            .head(100)
        )
        return data_top_n

    click_data = reactive.value(None)

    @reactive.calc
    def get_filtered_data():
        clicked = click_data()

        SEL_COLUMNS = [
            COL_AUTHOR_ID,
            COL_NGRAM_WORDS,
            COL_MESSAGE_TEXT,
            COL_MESSAGE_TIMESTAMP,
        ]
        COL_RENAME = ["Unique user ID", "N-gram content", "Post content", "Timestamp"]

        old2new = {k: v for k, v in zip(SEL_COLUMNS, COL_RENAME)}

        if clicked and isinstance(clicked, dict) and COL_NGRAM_ID in clicked:
            ngram_id = clicked[COL_NGRAM_ID]
            filtered = data_full.filter(pl.col(COL_NGRAM_ID) == ngram_id)

            filtered = filtered.with_columns(
                pl.col(COL_MESSAGE_TIMESTAMP).dt.strftime("%B %d, %Y %I:%M %p")
            )
            filtered = filtered.select(SEL_COLUMNS).rename(old2new)
            return filtered
        else:
            # Return empty dataframe with the right columns when nothing is clicked
            return data_full.select(SEL_COLUMNS).head(0)

    def on_point_click(trace, points, state):
        if not hasattr(points, "point_inds") or not points.point_inds:
            return

        # Extract ngram_id from customdata
        point_idx = points.point_inds[0]

        try:
            # Access customdata from the trace
            customdata = trace.customdata
            if customdata is not None and len(customdata) > point_idx:
                ngram_id = customdata[point_idx][
                    1
                ]  # ngram_id is second item in customdata
                click_data.set({COL_NGRAM_ID: ngram_id, "points": points})
            else:
                click_data.set(points)
        except Exception:
            click_data.set(points)

        fig_widget = trace.parent

        # Clear markers from previous clicks
        fig_widget = _remove_markers(figure_widget=fig_widget)

        # Add new green marker at clicked point
        fig_widget.add_scatter(
            x=[points.xs[0]],
            y=[points.ys[0]],
            mode="markers",
            marker=dict(size=15, color=CLICKED_COLOR),
            hoverinfo="skip",
            showlegend=False,
        )

    @render_widget
    def scatter_plot():
        # Use search-filtered data if search is active, otherwise use regular filtered data
        user_search = input.user_id_search() or ""
        content_search = input.ngram_content_search() or ""
        has_search = bool(user_search.strip() or content_search.strip())
        
        if has_search:
            df = get_search_filtered_stats()
        else:
            df = get_data()
            
        fig = plot_scatter(data=df)

        # Create FigureWidget directly from the figure
        fig_widget = go.FigureWidget(fig)

        # Store reference to figure widget for reset functionality
        current_figure_widget.set(fig_widget)

        # Attach click handler to all traces (one for each color group)
        for trace in fig_widget.data:
            trace.on_click(on_point_click)

        return fig_widget

    @render.text
    def data_info():
        # Get search values once to avoid multiple reactive calls
        user_search = input.user_id_search() or ""
        content_search = input.ngram_content_search() or ""
        has_search = bool(user_search.strip() or content_search.strip())
        
        if has_search:
            search_data = get_search_filtered_data()
            search_stats = get_search_filtered_stats()
            
            if search_data.is_empty():
                return "No results found for the current search criteria. Try adjusting your search."
            else:
                total_ngrams = len(search_stats)
                total_records = len(search_data)
                user_id_text = f"User ID: '{user_search}'" if user_search.strip() else ""
                content_text = f"Content: '{content_search}'" if content_search.strip() else ""
                search_text = " and ".join(filter(None, [user_id_text, content_text]))
                return f"Search results for {search_text}: {total_ngrams} unique n-grams, {total_records} total records."
        else:
            filtered = get_filtered_data()
            if filtered.is_empty():
                return "Showing summary (top 100 n-grams). Select a data point by clicking on the scatter plot above to show data for selected n-gram."
            else:
                total_reps = len(filtered)
                ngram_string = filtered["N-gram content"][0]
                return f"Ngram: {ngram_string}, Nr. total repetitions: {total_reps}"

    @render.data_frame
    def data_viewer():
        # Get search values once to avoid multiple reactive calls
        user_search = input.user_id_search() or ""
        content_search = input.ngram_content_search() or ""
        has_search = bool(user_search.strip() or content_search.strip())
        
        if has_search:
            # Show search results
            search_data = get_search_filtered_data()
            if search_data.is_empty():
                # Show empty dataframe with proper columns
                data_for_display = data_full.select([
                    COL_AUTHOR_ID, COL_NGRAM_WORDS, COL_MESSAGE_TEXT, COL_MESSAGE_TIMESTAMP
                ]).head(0).rename({
                    COL_AUTHOR_ID: "Unique user ID",
                    COL_NGRAM_WORDS: "N-gram content", 
                    COL_MESSAGE_TEXT: "Post content",
                    COL_MESSAGE_TIMESTAMP: "Timestamp"
                })
            else:
                # Show search results
                SEL_COLUMNS = [COL_AUTHOR_ID, COL_NGRAM_WORDS, COL_MESSAGE_TEXT, COL_MESSAGE_TIMESTAMP]
                COL_RENAME = ["Unique user ID", "N-gram content", "Post content", "Timestamp"]
                old2new = {k: v for k, v in zip(SEL_COLUMNS, COL_RENAME)}
                
                data_for_display = search_data.with_columns(
                    pl.col(COL_MESSAGE_TIMESTAMP).dt.strftime("%Y-%m-%d %H:%M:%S")
                ).select(SEL_COLUMNS).rename(old2new)
        else:
            # Original behavior - show clicked data or summary
            data_for_display = get_filtered_data()
            if data_for_display.is_empty():
                data_for_display = get_top_n_data()

        return render.DataGrid(data_for_display, width="100%")
