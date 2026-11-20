using MultiFunPlayer.Common;
using Newtonsoft.Json.Linq;
using Stylet;
using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Text;

namespace MultiFunPlayer.OutputTarget.ViewModels;

// Media status for the voice control tool: while remote commands are on, the current media path, position and
// play/pause state go out as a UDP datagram to 127.0.0.1:RemoteStatusPort once a second and at once on every change
// (a new file, play/pause, a seek), so the tool can follow what is happening in the video.
// Message: {"event": "media", "path": "<path or null>", "position": <s>, "duration": <s>, "playing": <bool>,
//           "scripts": ["<name of the script loaded on an axis>", ...]}
// The script names tell the tool which film it is when the path does not (a Stash stream: ".../scene/414/stream").
internal sealed partial class ButtplugOutputTarget :
    IHandle<MediaPathChangedMessage>, IHandle<MediaPositionChangedMessage>,
    IHandle<MediaPlayingChangedMessage>, IHandle<MediaDurationChangedMessage>, IHandle<ScriptChangedMessage>
{
    private readonly Dictionary<DeviceAxis, string> _mediaScripts = [];
    private readonly object _mediaStatusLock = new();
    private readonly Stopwatch _mediaPositionClock = Stopwatch.StartNew();
    private string _mediaPath;
    private double _mediaPosition;
    private double _mediaDuration;
    private bool _mediaPlaying;
    private int _remoteStatusPort = 23556;
    private readonly SemaphoreSlim _mediaStatusChanged = new(0, 1);

    public int RemoteStatusPort
    {
        get => _remoteStatusPort;
        set => SetAndNotify(ref _remoteStatusPort, Math.Clamp(value, 1024, 65535));
    }

    public void Handle(MediaPathChangedMessage message)
    {
        lock (_mediaStatusLock)
        {
            _mediaPath = message.Path;
            _mediaPosition = 0;
            _mediaPositionClock.Restart();
        }

        SignalMediaStatus();
    }

    public void Handle(MediaPositionChangedMessage message)
    {
        var position = message.Position.TotalSeconds;
        bool jumped;
        lock (_mediaStatusLock)
        {
            jumped = message.ForceSeek || Math.Abs(position - CurrentMediaPosition()) > 2;
            _mediaPosition = position;
            _mediaPositionClock.Restart();
        }

        if (jumped)
            SignalMediaStatus();
    }

    public void Handle(MediaPlayingChangedMessage message)
    {
        lock (_mediaStatusLock)
        {
            _mediaPosition = CurrentMediaPosition();
            _mediaPositionClock.Restart();
            _mediaPlaying = message.IsPlaying;
        }

        SignalMediaStatus();
    }

    public void Handle(ScriptChangedMessage message)
    {
        lock (_mediaStatusLock)
        {
            if (message.Script?.Name is { Length: > 0 } name)
                _mediaScripts[message.Axis] = name;
            else
                _mediaScripts.Remove(message.Axis);
        }

        SignalMediaStatus();
    }

    public void Handle(MediaDurationChangedMessage message)
    {
        lock (_mediaStatusLock)
            _mediaDuration = message.Duration.TotalSeconds;
    }

    // the last reported position moved on by the time since, while playing (the player reports a few times a second)
    private double CurrentMediaPosition()
        => _mediaPlaying ? _mediaPosition + _mediaPositionClock.Elapsed.TotalSeconds : _mediaPosition;

    private void SignalMediaStatus()
    {
        try { _mediaStatusChanged.Release(); }
        catch (SemaphoreFullException) { }
    }

    private string BuildMediaStatus()
    {
        lock (_mediaStatusLock)
        {
            return new JObject
            {
                ["event"] = "media",
                ["path"] = _mediaPath,
                ["position"] = Math.Round(CurrentMediaPosition(), 2),
                ["duration"] = Math.Round(_mediaDuration, 2),
                ["playing"] = _mediaPlaying,
                ["scripts"] = new JArray(_mediaScripts.Values.Distinct().Order()),
            }.ToString(Newtonsoft.Json.Formatting.None);
        }
    }

    private async Task RunMediaStatusSenderAsync(CancellationToken token)
    {
        try
        {
            using var client = new UdpClient();
            while (!token.IsCancellationRequested)
            {
                try
                {
                    var bytes = Encoding.UTF8.GetBytes(BuildMediaStatus());
                    await client.SendAsync(bytes, bytes.Length, new IPEndPoint(IPAddress.Loopback, _remoteStatusPort));
                }
                catch (SocketException e)
                {
                    // nobody listening is fine (the tool is not running); anything else is worth a line in the log
                    if (e.SocketErrorCode != SocketError.ConnectionReset)
                        Logger.Debug(e, "Media status send failed");
                }

                await _mediaStatusChanged.WaitAsync(TimeSpan.FromSeconds(1), token);
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception e)
        {
            Logger.Warn(e, "Media status sender stopped");
        }
    }
}
