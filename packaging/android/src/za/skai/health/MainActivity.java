package za.skai.health;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ContentValues;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.provider.MediaStore;
import android.text.InputType;
import android.util.Base64;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.io.OutputStream;

/**
 * SKAI for Android: shows the SKAI web app (served by the SKAI server, hosted online
 * or on a laptop on the same Wi-Fi) full screen, with file uploads and downloads.
 * The risk models run on the server, not on the phone.
 */
public class MainActivity extends Activity {
    // Replaced by build_apk.py with the hosted SKAI address (may be empty: then the app asks)
    static final String DEFAULT_SERVER = "@SERVER_URL@";
    static final int FILE_REQUEST = 1;

    WebView web;
    LinearLayout errorView;
    TextView errorText;
    ValueCallback<Uri[]> pendingUpload;
    SharedPreferences prefs;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences("skai", MODE_PRIVATE);

        FrameLayout root = new FrameLayout(this);
        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        errorView = buildErrorView();
        root.addView(errorView, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);          // sign-in sessions, theme and language are kept in localStorage
        s.setAllowFileAccess(false);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setUserAgentString(s.getUserAgentString() + " SKAIAndroid/1.0");
        web.addJavascriptInterface(new Bridge(), "SKAIAndroid");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri url = request.getUrl();
                Uri home = Uri.parse(server());
                if (url.getHost() != null && url.getHost().equals(home.getHost())) return false;
                startActivity(new Intent(Intent.ACTION_VIEW, url));   // other sites open in the phone's browser
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) showError("SKAI could not be reached at\n" + server()
                        + "\n\nCheck your internet connection. If SKAI runs on a laptop, the phone must be on the same Wi-Fi.");
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (!url.startsWith("about:")) errorView.setVisibility(View.GONE);
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (pendingUpload != null) pendingUpload.onReceiveValue(null);
                pendingUpload = callback;
                try {
                    startActivityForResult(params.createIntent(), FILE_REQUEST);
                } catch (Exception e) {
                    pendingUpload = null;
                    toast("No app found to choose a file");
                    return false;
                }
                return true;
            }
        });

        if (server().isEmpty()) askServer(true);
        else load();
    }

    String server() {
        return prefs.getString("server", DEFAULT_SERVER.startsWith("@") ? "" : DEFAULT_SERVER);
    }

    void load() {
        errorView.setVisibility(View.GONE);
        web.loadUrl(server());
    }

    void showError(String message) {
        errorText.setText(message);
        errorView.setVisibility(View.VISIBLE);
    }

    LinearLayout buildErrorView() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setPadding(60, 60, 60, 60);
        box.setBackgroundColor(Color.parseColor("#0f172a"));
        box.setVisibility(View.GONE);

        TextView title = new TextView(this);
        title.setText("SKAI");
        title.setTextColor(Color.parseColor("#38bdf8"));
        title.setTextSize(34);
        title.setGravity(Gravity.CENTER);
        box.addView(title);

        errorText = new TextView(this);
        errorText.setTextColor(Color.parseColor("#e2e8f0"));
        errorText.setTextSize(16);
        errorText.setGravity(Gravity.CENTER);
        errorText.setPadding(0, 30, 0, 40);
        box.addView(errorText);

        Button retry = new Button(this);
        retry.setText("Retry");
        retry.setOnClickListener(v -> load());
        box.addView(retry);

        Button change = new Button(this);
        change.setText("Change server address");
        change.setOnClickListener(v -> askServer(false));
        box.addView(change);
        return box;
    }

    void askServer(boolean firstTime) {
        EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint("https://your-skai-address");
        input.setText(server());
        AlertDialog.Builder dialog = new AlertDialog.Builder(this)
                .setTitle("SKAI server address")
                .setMessage("Enter the address where SKAI is running (the hosted link, or http://<laptop-IP>:8000 on the same Wi-Fi).")
                .setView(input)
                .setPositiveButton("Connect", (d, w) -> {
                    String url = input.getText().toString().trim();
                    if (!url.isEmpty() && !url.startsWith("http")) url = "https://" + url;
                    while (url.endsWith("/")) url = url.substring(0, url.length() - 1);
                    prefs.edit().putString("server", url).apply();
                    if (url.isEmpty()) askServer(true); else load();
                });
        if (!firstTime) dialog.setNegativeButton("Cancel", null);
        dialog.setCancelable(!firstTime).show();
    }

    void toast(String text) {
        Toast.makeText(this, text, Toast.LENGTH_LONG).show();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == FILE_REQUEST && pendingUpload != null) {
            pendingUpload.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data));
            pendingUpload = null;
        }
    }

    @Override
    public void onBackPressed() {
        if (errorView.getVisibility() != View.VISIBLE && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    /** Called by the web app (window.SKAIAndroid) to save reports, letters and data exports. */
    class Bridge {
        @JavascriptInterface
        public void saveFile(String name, String mime, String base64) {
            try {
                byte[] bytes = Base64.decode(base64, Base64.DEFAULT);
                ContentValues values = new ContentValues();
                values.put(MediaStore.Downloads.DISPLAY_NAME, name.replaceAll("[\\\\/:*?\"<>|]", "_"));
                values.put(MediaStore.Downloads.MIME_TYPE, mime);
                Uri uri = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
                try (OutputStream out = getContentResolver().openOutputStream(uri)) {
                    out.write(bytes);
                }
                runOnUiThread(() -> {
                    toast("Saved to Downloads: " + name);
                    Intent open = new Intent(Intent.ACTION_VIEW).setDataAndType(uri, mime)
                            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    try { startActivity(open); } catch (Exception ignored) { /* no viewer for this type */ }
                });
            } catch (Exception e) {
                runOnUiThread(() -> toast("Could not save " + name));
            }
        }
    }
}
