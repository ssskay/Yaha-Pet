import os
import sys
import json
import time
import wave
import re
import urllib.request
import urllib.error
from PyQt6.QtWidgets import QApplication, QWidget, QSystemTrayIcon, QMenu, QLabel, QMenuBar, QMessageBox
from PyQt6.QtCore import Qt, QSize, QPoint, QUrl, QPropertyAnimation, QTimer, QEasingCurve, QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QGuiApplication, QPixmap, QAction, QActionGroup, QDesktopServices
from PyQt6.QtMultimedia import QSoundEffect
from pathlib import Path
import random 

def _excepthook(exc_type, exc_value, exc_tb):
    """Log unhandled exceptions instead of letting PyQt6 abort the app."""
    import traceback
    traceback.print_exception(exc_type, exc_value, exc_tb)

sys.excepthook = _excepthook

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

#Local pack: licensed-for-personal-use extras (the Shadowverse voice lines, the
#anime-cut dance/look moments) that must never ship in the repo or the public
#DMG. They live outside assets/, mirroring its layout, in the first of:
#  $YAHA_LOCAL_PACK  ->  ~/Library/Application Support/Yaha-Pet/local-pack  ->  ./local-pack
#Install into Application Support with scripts/install-local-pack.sh. Anything
#missing just falls back to the bundled assets/, or the feature quietly hides.
def _local_pack_roots() -> 'list[str]':
    roots = []
    if os.environ.get("YAHA_LOCAL_PACK"):
        roots.append(os.environ["YAHA_LOCAL_PACK"])
    roots.append(os.path.join(Path.home(), "Library", "Application Support", "Yaha-Pet", "local-pack"))
    roots.append(os.path.abspath("local-pack"))
    return roots

def asset_path(relative_path: str) -> str:
    """Path to an asset, preferring the local pack over the bundled copy."""
    for root in _local_pack_roots():
        candidate = os.path.join(root, relative_path)
        if os.path.exists(candidate):
            return candidate
    return resource_path(relative_path)

def coanim_available(set_name: str) -> bool:
    """True when a co-animation set's frames exist (bundled or local pack)."""
    frames_dir = Path(asset_path(f'assets/coanimations/{COANIM_SETS[set_name]["frames_dir"]}'))
    return frames_dir.exists() and any(frames_dir.glob('*.png'))

#Voice lines: short in-character clips under assets/<name>/sounds/voice, named
#NN_label.wav (01_greeting.wav -> "greeting"). They are gated three ways:
#  - Mute All / per-character mute (self.mutesounds), like every other sound
#  - the global "voice_lines" config key + the Voice Lines menu item
#  - a per-character override, config_data[<name>]["voice_lines"]
#Ambient lines (spawn greeting, victory, thanks) additionally roll the existing
#per-character sound_chance; lines that answer a direct user action (grab, hard
#throw, Say hi, kick) always speak, matching how "grabbed" sfx already behave.
voice_lines_flag : bool = True

def voice_enabled(name: 'str | None' = None) -> bool:
    if(not voice_lines_flag):
        return False
    if(name is None):
        return True
    return bool(config_data.get(name, {}).get("voice_lines", True))

#Which voice labels can answer each moment. One entry is picked at random, so
#the mix IS the weighting — repeat a label to make it likelier. Every clip we
#have is reachable from here. A label that isn't installed is simply skipped, so
#trimming clips during vetting just narrows the pool it belongs to.
VOICE_POOLS : dict = {
    "spawn":   ["greeting"],
    "launch":  ["start1", "start2"],                 # only the first pet of the session
    #Being picked up is usually a yelp, but not always — some of them are
    #delighted to be held, which is the whole joke.
    "grab":    ["hurt1", "hurt2", "hurt3", "hurt4", "hurt5",
                "greeting", "impressed", "thinking", "taunt"],
    "crash":   ["shocked", "shocked", "shocked", "apology", "impressed"],
    "bounce":  ["apology"],                          # clipped a wall mid-flight
    "idle":    ["thinking", "taunt", "apology"],     # muttering to themselves
    "dance":   ["victory", "evolve1", "evolve2", "evolve3"],
    "coanim":  ["thanks", "thanks", "impressed"],
    "despawn": ["concede1", "concede2"],
}

#Voice lines that stand in for a missing animation sfx. Vetting removed most of
#chiikawa's and all of hachiware's original solo sounds, so their walks and jumps
#had gone silent; these fill that space in-character rather than with nothing.
#Only consulted when the animation's own .wav is absent, so any character that
#kept its sfx is untouched.
ANIM_VOICE_FALLBACK : dict = {
    "walkleft":  ["thinking"],                      # humming to themselves as they wander
    "walkright": ["thinking"],
    "jumpleft":  ["evolve1", "evolve2", "evolve3"],  # the closest thing to a "hup!"
    "jumpright": ["evolve1", "evolve2", "evolve3"],
}

session_started : bool = False # False until the first pet of the session speaks

#Two dials the user can move from the menu, both persisted to config.json.
#
#CHATTINESS scales how often AMBIENT noise happens - random animation sounds,
#idle mutters, victory lines. It multiplies the per-character sound_chance, so a
#character tuned quiet in config stays relatively quieter at every setting.
#Deliberately defaults below "normal": the pets were talking over each other.
#Sounds that answer something the user just did (grabbing, throwing, Say hi,
#kicking) ignore it - those aren't chatter, they're feedback.
#
#VOLUME scales every player. The per-player base levels below stay as they were;
#this rides on top.
CHATTINESS_LEVELS = [("Quiet", 0.2), ("Low", 0.45), ("Normal", 0.7), ("Chatty", 1.0)]
VOLUME_LEVELS = [("25%", 0.25), ("50%", 0.5), ("75%", 0.75), ("100%", 1.0)]
chattiness : float = 0.45   # "Low"
master_volume : float = 1.0

def effective_chance(name: str) -> float:
    #Per-character chattiness from config, scaled by the global dial.
    return config_data.get(name, {}).get("sound_chance", 1.0) * chattiness

def vol(base: float) -> float:
    #A player's base level, scaled by the global volume dial.
    return max(0.0, min(1.0, base * master_volume))

#SETTINGS PERSISTENCE
USER_CONFIG_PATH = os.path.join(Path.home(), "Library", "Application Support",
                                "Yaha-Pet", "config.json")

def save_settings():
    """Write the menu-controlled settings back to the user's config so they
    survive a restart. Everything else already in that file (per-character
    tuning, co-animation cooldowns) is preserved - we re-dump the loaded doc
    with only our three keys updated."""
    config_data["voice_lines"] = voice_lines_flag
    config_data["chattiness"] = chattiness
    config_data["volume"] = master_volume
    try:
        os.makedirs(os.path.dirname(USER_CONFIG_PATH), exist_ok=True)
        with open(USER_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2)
        print(f"settings saved to {USER_CONFIG_PATH}")
    except OSError as e:
        print(f"could not save settings: {e}")  # never let this break playback

def set_chattiness(value: float):
    global chattiness
    chattiness = value
    print(f"chattiness -> {value}")
    save_settings()

def set_volume(value: float):
    global master_volume
    master_volume = value
    print(f"volume -> {value}")
    save_settings()

def toggle_voice_lines(enabled: bool):
    global voice_lines_flag
    voice_lines_flag = enabled
    print(f"voice lines {'on' if enabled else 'off'}")
    save_settings()

#Functions
def close_app():
    app.quit()


def get_size():
    primary_screen = QGuiApplication.primaryScreen()
    screen_size = primary_screen.size()
    return screen_size

def get_size_for_characters():
    size = get_size()
    width = size.width()//10
    height = size.height()//10
    size = QSize(width, height)
    return size

def resize_to_current_screen():
    screen_size = get_size()
    screen_width = screen_size.width()
    screen_height = screen_size.height()
    yahawindow.resize(screen_width, screen_height)

def get_taskbar_height():
    
    screen = QGuiApplication.primaryScreen()
    available_height = screen.availableGeometry().height()
    
    return available_height # The available height is the taskbar's height

def say_hi_message():
    if(len(characters_names)>0):
        name = random.choice(characters_names)
        icon_path = QIcon(resource_path(f'assets/{name}/icons/icon.png'))
        yaha_tray.showMessage(f'{name} says:','Hi!', icon_path, 500)

        #Prefer the character's own greeting voice line; fall back to hi.wav when
        #voice lines are off, missing, or the character is muted.
        char = next((c for c in characters if c is not None and c.getName() == name), None)
        if(char is not None and char.play_voice("greeting", ignore_chance=True)):
            return
        if(muteall_flag):
            return
        #Setting the sound
        sound_path = resource_path(f'assets/{name}/sounds/hi.wav')
        sound.setSource(QUrl.fromLocalFile(sound_path))
        sound.setVolume(vol(0.5))
        sound.setLoopCount(1)
        sound.play()
    else:
        yaha_tray.showMessage('Wait!','You have not spawned anyone yet!', QSystemTrayIcon.MessageIcon.Information, 500)

class Character(QWidget):
    def __init__(self, name: str, size: QSize):
        super().__init__(parent = None)
        #Basic attributes
        self.name = name
        self.drag = False
        self.char_size = size
        self.first = True # Its the first time it spawns, useful when calling the function set_sprite.
        self.associated_stop_button : QAction = None
        self.associated_play_button : QAction = None
        #self.start_time : float
        #self.end_time : float # - BENCHMARKING PURPOSES

        #Sound player
        self.soundplayer = QSoundEffect()
        self.soundplayer.setVolume(0.5)
        self.soundplayer.setLoopCount(1)
        self.mutesounds : bool = False

        #Persistent player for "grabbed" sounds. A new QSoundEffect was
        #previously created per grab and set to delete itself when playback
        #stopped, but on Qt 6.10 its destructor re-emits playingChanged and
        #the double deleteLater() crashed the app (SIGABRT).
        self.grabplayer = QSoundEffect()
        self.grabplayer.setVolume(1)
        self.grabplayer.setLoopCount(1)

        #Voice player, kept separate from the animation and grab players so a
        #voice line can land without cutting an animation's own sound short.
        self.voiceplayer = QSoundEffect()
        self.voiceplayer.setVolume(0.6)
        self.voiceplayer.setLoopCount(1)

        #Voice lines: assets/<name>/sounds/voice/NN_label.wav -> {"greeting": path}
        self.voice_lines : dict[str, str] = {}
        voice_path = Path(asset_path(f'assets/{self.name}/sounds/voice'))
        if voice_path.exists():
            for file in sorted(voice_path.glob("*.wav")):
                label = file.stem.split('_', 1)[-1] # "01_greeting" -> "greeting"
                self.voice_lines[label] = str(file)
            print(f"{self.name} voice lines: {sorted(self.voice_lines)}")

        #Sound Effects
        self.grabbed_soundeffects = [] # Paths of the sound effects available when the character gets grabbed with mouse
        sound_effect_path = Path(resource_path(f'assets/{self.name}/sounds'))
        if sound_effect_path.exists():
            for file in sound_effect_path.iterdir():
                file_name = file.name
                if(file_name[0:7] == "grabbed" and file_name[-4:] == ".wav"):
                    self.grabbed_soundeffects.append(str(file))
                    print(file_name)
            print(self.grabbed_soundeffects)


        #Animation variables
        self.onanimation = False  # Its not on animation, useful to avoid clicks when falling
        self.animation = None 
        self.before_anim_pos : QPoint # Save position 
        self.walktocoord = QPoint(0,self.pos().y()) # Coordinate that the character will walk to during their animation.
        self.animationnames = [] # Animation names for preloading purposes 
        self.randomtimer = QTimer() # Timer for random animations

        self.chosen_grabbed_image : bool = False
        self.grabbed_image : QPixmap = None;

        #Throw physics variables — flick a character on release and it goes flying
        self.drag_history = [] # recent (timestamp, QPoint) samples while dragging
        self.physics_timer = QTimer()
        self.physics_timer.timeout.connect(self._physics_step)
        self.velocity = [0.0, 0.0] # px/s
        self.bounced_voice = False # has this flight already used its wall-bounce line?
        self.pos_f = [0.0, 0.0]    # float position accumulator

        #Shake animation variables
        self.held_timer = QTimer() # Timer to start shake animation
        self.start_shake : bool = False # Boolean for confirmation to start shake animation
        self.shaken_image = resource_path(f'assets/{self.name}/sprites/shaken.png')
        
        #pre-loading animation frames
        self.frames : dict[str, list[QPixmap]] = {} # Example: "Dance", "frame1,frame2,frame3"
        self.current_frame_idx = 0 
        self.anim_idx : dict[str,int] = {} # Example: Dance, 1
        self.current_anim_name = ''

        #Saving sprites
        self.sprites : dict[str, list[QPixmap]] = {}

    
        for sprite in Path(resource_path(f'assets/{self.name}/sprites')).iterdir():
            spritename = ""
            if(sprite.name[:5] == "spawn"):  
                spritename = sprite.name[:5]
            if(sprite.name[:7] == "falling"):
                spritename = sprite.name[:7]
            if(sprite.name[:7] == "grabbed"):
                spritename = sprite.name[:7]
            if(sprite.name[:4] == "jump"):
                
                spritename = sprite.name.split('.')[0]
                
            if(spritename != ""):
                img = self.convert_sprite_to_pixmap(sprite)
                if(spritename not in self.sprites):
                    self.sprites[spritename] = []
                self.sprites[spritename].append(img)
                
            else:
                pass 
       
            
        #print(self.sprites["spawn"])

        #Setting the timer for random animations
        self.frame_timer = QTimer()
        self.frame_timer.timeout.connect(self.next_frame)

        #Setting the timer for jump animation
        self.jump_frame_timer = QTimer()
        

        self.modified_animationlist = totalanimations.copy()
        #Valid random animations
        if self.name in self.modified_animationlist:
            unwanted_random_animations = ["walkright", "walkleft", "falling", "jump"]
            for name in unwanted_random_animations:
                try:
                    self.modified_animationlist[self.name].remove(name)
                    print("Removed: ",name," from random animations")
                except ValueError:
                    continue

        #Direction walk-animation variables
        self.direction : int = 0
        
        #Idle Timer 
        self.idle_timer = QTimer()
        self.idle_timer.timeout.connect(self.try_animation)

        #Creating label for containing the sprite
        self.label = QLabel(parent = self) 
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter) # Align with parent
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True) # Doesnt consume click entries

        #Character window flags: Always on top, no text and no taskbar icon, and receive click inputs
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowFlag(Qt.WindowType.Tool)
        #Never steal keyboard focus: clicking/dragging a pet should not push
        #the app you were working in behind other windows.
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        #macOS: keep the pet visible even when another app is focused.
        #Without this, Tool windows hide when the app deactivates ("losing" the pet).
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)

        #Widget Attributes
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True) # no backg
        self.resize(self.char_size) # same size as parent

        #Start the random animation timer
        self.start_random_timer()

    def start_random_timer(self):
        random_time = random.randrange(3000,10000)
        #Per-character pacing: >1.0 makes a character act (and thus vocalize) less often
        scale = config_data.get(self.name, {}).get("animation_interval_scale", 1.0)
        self.randomtimer.start(int(random_time * scale))
        self.randomtimer.timeout.connect(self.try_animation)
            
    def start_anim(self, animname: str):
        animation_properties = config_data.get(self.name, {}).get("animations", {}).get(animname, {})
        fps = animation_properties.get("fps", 40)
        print("Using ",fps," FPS on '",animname,"'/ animation.")
        if(not self.onanimation and not self.drag):
            self.before_anim_pos = self.pos()
            if animname not in self.frames: # If animation hasnt loaded yet
                self.preload_animations(animname)
            if self.frames.get(animname): # If frames are loaded
                self.current_anim_name = animname # Set animation to current animation
                self.current_frame_idx = 0
                self.onanimation = True # avoid clicking
                self.frame_timer.start(int(1000 / fps))
                if(self.mutesounds == False):
                    self.play_animsound(animname)
    def play_animsound(self, animname:str ):
        #Per-character chattiness: sound_chance < 1.0 randomly skips some sounds
        chance = effective_chance(self.name)
        if(random.random() > chance):
            print(f"({self.name} kept quiet this time, chance={chance:.2f})")
            return
        self.soundplayer.setMuted(False)
        self.soundplayer.setVolume(vol(0.5))
        self.soundplayer.setLoopCount(1)
        soundpath = resource_path(f'assets/{self.name}/sounds/{animname}.wav')
        print(f"sound: {soundpath}")
        if(Path.exists(Path(soundpath))):
            self.soundplayer.setSource(QUrl.fromLocalFile(soundpath))
            self.soundplayer.play()
        elif(animname in ANIM_VOICE_FALLBACK):
            #No sfx for this animation (vetted out, or never existed) - let them
            #cover it with their voice instead. The chance roll already passed.
            self.play_voice(*ANIM_VOICE_FALLBACK[animname], ignore_chance=True)
    def stop_current_sound(self):
        self.soundplayer.stop()

    def play_voice(self, *labels: str, ignore_chance: bool = False) -> bool:
        """Speak one of the named voice lines (random pick when several are given,
        e.g. play_voice("concede1", "concede2")). Returns whether anything played,
        so callers can fall back to an older sound. Honours mute, the voice_lines
        toggle, and — unless ignore_chance — the per-character sound_chance."""
        if(self.mutesounds or not voice_enabled(self.name)):
            return False
        choices = [self.voice_lines[label] for label in labels if label in self.voice_lines]
        if(not choices):
            return False
        chance = effective_chance(self.name)
        if(not ignore_chance and random.random() > chance):
            print(f"({self.name} swallowed a voice line, chance={chance:.2f})")
            return False
        self.voiceplayer.stop()
        self.voiceplayer.setMuted(False)
        self.voiceplayer.setVolume(vol(0.6))
        self.voiceplayer.setSource(QUrl.fromLocalFile(random.choice(choices)))
        self.voiceplayer.play()
        return True

    def grab_sound_pool(self) -> list:
        """The "grabbed" sfx plus, when voice lines are on, everything in the "grab"
        voice pool — so being picked up can come out as a squeak, a yelp, or a
        genuinely pleased noise. Rebuilt per grab so the Voice Lines menu item
        takes effect immediately."""
        pool = list(self.grabbed_soundeffects)
        if(voice_enabled(self.name)):
            pool += [self.voice_lines[label] for label in VOICE_POOLS["grab"]
                     if label in self.voice_lines]
        return pool

    def try_animation(self):
        screen_width = get_size().width()
        screen_height = get_size().height()
        if(self.pos().x() < 0 or self.pos().x()>screen_width or self.pos().y()<0 or self.pos().y()>screen_height):
            self.move(self.clamp_to_screen(0,0))
        if(not self.onanimation and not self.drag):
            #Sometimes they just mutter to themselves instead of moving. Only
            #skips the animation if a line actually played (mute, the voice
            #toggle and sound_chance can all veto it).
            if(random.random() < 0.25 and self.play_voice(*VOICE_POOLS["idle"])):
                return
            # Fixed behavior roll: original code always jumped (roll<=100 was always true).
            # Now: 40% jump, 30% walk, 30% random animation (dance etc.)
            roll = random.randrange(0,100)
            if(roll < 40):
                self.start_jumpanimation(screen_width)
            elif(roll < 70):
                self.start_walkanimation()
            else:
                if(len(self.modified_animationlist[self.name])>0):
                    chosen_animation = random.choice(self.modified_animationlist[self.name])
                    self.start_anim(chosen_animation)
                else:
                    self.start_walkanimation()
    def start_jumpanimation(self, screen_width):
        self.onanimation = True
        direction_roll = random.randint(0,1)
        if(direction_roll == 1 and self.pos().x()>= screen_width- 100): # if right and too close to border, go left instead
            direction_roll = 0
        if(direction_roll == 0 and not self.pos().x() <= 100 ): # If 0 and not too close to border go left
            end_range_x = self.pos().x() - random.randint(0,100) 
            if(end_range_x > screen_width):
                end_range_x = screen_width - 1
            
        else:
            end_range_x = self.pos().x() +  random.randint(0,100)
            if(end_range_x < 0):
                end_range_x = 1
        
        jump_height = random.randint(50, 300)

        distance_x = abs(end_range_x - self.pos().x())
        if(direction_roll == 0): # Calculate the point to travel to in the first half of the jump
            first_half_x_point = abs(end_range_x + int(distance_x/2))
            chosen_direction = "jumpleft"
        else:
            first_half_x_point = abs(end_range_x - int(distance_x/2))
            chosen_direction = "jumpright"

        time = jump_height*10
        self.animation = QPropertyAnimation(self, b"pos")
        self.animation.setDuration(time)
        self.animation.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.animation.setTargetObject(self)
        self.animation.setStartValue(QPoint(self.pos()))
        self.animation.setEndValue(QPoint(first_half_x_point, self.pos().y() - jump_height - self.height()))
        self.animation.finished.connect(lambda: self.end_jumpanimation(time, end_range_x))
        
        jump_dir_left = Path(resource_path("assets/{self.name}/animations/jumpleft"))
        jump_dir_right = Path(resource_path("assets/{self.name}/animations/jumpright"))
        if(not "jumpleft" in self.frames and jump_dir_left.exists()):
            self.preload_animations("jumpleft")
        if(not "jumpright" in self.frames and jump_dir_right.exists()):
            self.preload_animations("jumpright")
        if(chosen_direction in self.frames):
            frames = self.frames.get(chosen_direction,[])
            total_frames = len(frames)
            if(total_frames == 0):
                return
            self.current_frame_idx = 1
            time_for_frame = int(time*2/total_frames)
            self.jump_frame_timer.start(time_for_frame)
            self.jump_frame_timer.timeout.connect(lambda: self.next_jump_frame(frames))
        else:
            if(direction_roll == 0 and "jumpleft" in self.sprites):
                jump_sprite = self.sprites["jumpleft"][0]
            else:
                if("jumpright" in self.sprites): 
                    jump_sprite = self.sprites["jumpright"][0]
                else:
                    print("jump sprites not found")
                    self.stop_current_animation()
                    return
            self.setLabelImage(jump_sprite)
            self.animation.start()
            
        
    def next_jump_frame(self, frames):
        
        if(self.current_frame_idx < len(frames) and self.current_frame_idx>=0):
            tuple = frames[self.current_frame_idx] # Access the tuple with image name and pixmap
            self._set_masked_pixmap(tuple[1])
            self.current_frame_idx += 1
            if "start" in tuple[0]:
                self.animation.start()
        else:
            self.jump_frame_timer.stop()

    def end_jumpanimation(self, time: int, end_range_x: int):
        self.animation = QPropertyAnimation(self, b"pos")
        self.animation.setDuration(time)
        self.animation.setEasingCurve(QEasingCurve.Type.InQuad)
        self.animation.setTargetObject(self)
        self.animation.setStartValue(QPoint(self.pos()))
        self.animation.setEndValue(QPoint(end_range_x, get_taskbar_height()-self.height()))
        self.animation.start()
        self.animation.finished.connect(self.stop_current_animation)

    def start_walkanimation(self ):
        screen_width = get_size().width()
        min_movement_distance = 100      

        self.roll_direction = random.randrange(0, 2) #If 0 go left, if 1 go right
        
        if(self.roll_direction == 0):
            start_range = 0
            end_range = self.pos().x() - min_movement_distance
        
        else:
            start_range = self.pos().x() + min_movement_distance
            end_range = screen_width-self.width()

        # Make sure we have at least one walk animation loaded.
        left_walk_dir = Path(resource_path(f'assets/{self.name}/animations/walkleft'))
        right_walk_dir = Path(resource_path(f'assets/{self.name}/animations/walkright'))
        if "walkleft" not in self.frames and left_walk_dir.exists():
            self.preload_animations("walkleft")
        if "walkright" not in self.frames and right_walk_dir.exists():
            self.preload_animations("walkright")
        
        # If none of the walk animations exist, there is nothing to do.
        if "walkleft" not in self.frames and "walkright" not in self.frames:
            return

        if(start_range<end_range  ):
            if(self.roll_direction == 0):
                self.start_anim("walkleft")
                chosen_walk_animation = "walkleft" if "walkleft" in self.frames else "walkright"
            else:
                self.start_anim("walkright")
                chosen_walk_animation = "walkright" if "walkright" in self.frames else "walkleft"

            self.start_anim(chosen_walk_animation)
            possible_direction = random.randrange(start_range,end_range) 
            
            self.walktocoord = QPoint(possible_direction, self.pos().y())
        
        else: # If invalid range, dont start any animation and try again later.
            return


        time = int(abs(self.walktocoord.x()-self.pos().x()))
        time = int(5*time) # Convert to int to avoid bugs
        self.animation = QPropertyAnimation(self, b"pos")
        self.animation.setDuration(time) # Time it takes the animation to be completed
        self.animation.setTargetObject(self) # Widget as the target for the animation
        self.animation.setStartValue(QPoint(self.pos())) # Current pos as start
        self.animation.setEndValue(QPoint(self.walktocoord.x(), self.pos().y())) # Same y coord.
        self.animation.finished.connect(self.stop_current_animation)
        self.animation.finished.connect(self.stop_current_sound) 
        self.animation.start()
    def next_frame(self):
        frames = self.frames.get(self.current_anim_name, [])   # Get the frames from the current animation
        if self.current_frame_idx<len(frames) and self.current_frame_idx>=0 : # While the current frame is still valid
            
            current_frame = frames[self.current_frame_idx][1]
            if(self.current_anim_name != "walkleft" and self.current_anim_name != "walkright"):
                #Compensating for size change between images
                prevpos = self.pos()
                prevsize = self.size()

                
                newsize = current_frame.size()

                deltawidth = newsize.width() - prevsize.width()
                deltaheight = newsize.height() - prevsize.height()
                new_pos = QPoint(
                    prevpos.x() - deltawidth // 2,
                    prevpos.y() - deltaheight // 2
                )
                self.move(new_pos)

            self._set_masked_pixmap(current_frame) # Set the frame, resize, and mask to visible pixels
            #print(self.walktocoord.x())
            self.current_frame_idx += 1
            
        else: # If the current frame doesnt exist

            anim_name = self.current_anim_name # stop_current_animation clears it; keep it for the walk check below
            if(self.animation != None and self.animation.state() == QPropertyAnimation.State.Running): # If animation is still running, repeat the frames
                self.current_frame_idx = 1
                print("reseted frame")
            else:
                print("reached")
                self.stop_current_animation()


            if(anim_name != "walkleft" and anim_name != "walkright"):  # Avoid changing the position for the walk ainmation
                self.move(self.before_anim_pos) # Changes the position in order to adjust for different image sizes
    def stop_current_animation(self):
        #Clear the animation name up front: it also decides whether a victory line
        #is owed, and stop_current_animation is reachable from jump/walk callbacks
        #that would otherwise re-fire a stale dance's line.
        finished_anim = self.current_anim_name
        self.current_anim_name = ''
        self.stop_current_sound()
        if(self.animation != None): # None until the first jump/walk — a tray-triggered dance can end before then
            self.animation.stop()
        self.frame_timer.stop()  # Stop the timer
        self.setDefaultLabel() # Set default animation
        self.onanimation = False # Animation ended
        #Any dance (dance, danceswirl, tapdance) earns a victory line.
        if("dance" in finished_anim):
            self.play_voice(*VOICE_POOLS["dance"])

    def blockAnimations(self):
        if(self.randomtimer.isActive()):
            print("stopped")
            self.randomtimer.stop()
            self.stop_current_animation()
            self.associated_stop_button.setText(f'{self.name} (click to enable)')
        else:
            self.randomtimer.start()
            self.associated_stop_button.setText(f'{self.name} (click to disable)')
    def convert_sprite_to_pixmap(self, dir):
        target: QSize = self.char_size # Set target size for images 
        img = QPixmap(str(dir)).scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        return img
    def preload_animations(self, animname):
        base_path = resource_path(f'assets/{self.name}/animations/{animname}')
        base = Path(base_path)

        files = sorted(base.glob("*.png"), key=lambda f: int(f.stem.split('-')[0])) # Sort files by number
  
        target: QSize = self.char_size # Set target size for images 
        width = target.width()
        height = target.height()

        if animname == "dance":  # was a `match` statement; changed for Python 3.9 compatibility on macOS
            width = int(width*1.5)
            height = int(height*1.5)
        target = QSize(width,height)
            
        loaded: list[tuple[str, QPixmap]] = []
        
        print("animname:", animname)
        for f in files:
            
            filename = f.name
            reader = QPixmap(str(f)).scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)  # QPixmap ready
            loaded.append((filename, reader)) # Append each image and name to  frames
        self.frames[animname] = loaded # Set the image list to the corresponding animation
        self.anim_idx[animname] = 0

        if(animname[:4] == "walk" or animname[:4] == "jump"):
            reverse_loaded: list[tuple[str, QPixmap]] = []
            reversed_filename: str;
            print("REVERSING::: ")
            print(animname)
            print(animname[-4:])
            if(animname[-5:]) == "right":
                reversed_filename = animname[:4]+"left"
            else:
                reversed_filename = animname[:4]+"right"
            print("reversed: ", reversed_filename)
            if(reversed_filename in self.frames):
                pass
            else:
                for name, pixmap in loaded:
                    pixmap = pixmap.toImage()
                    pixmap = QPixmap.fromImage(pixmap.mirrored(True, False))
                    reverse_loaded.append((name, pixmap))
                print("reversed")
                self.frames[reversed_filename] = reverse_loaded
                self.anim_idx[reversed_filename] = 0
        
    def preload_allanimations(self):
        for animation in totalanimations[self.name]:
            self.preload_animations(animation)
    def set_sprite(self, filename: str):
        # Initial widget position at center, above task bar
        posinicialx = get_size().width()//2
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        #Creating pixmap that contains the image and scales it to char_size
        pix = QPixmap(filename)
        scaled = pix.scaled(self.char_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation )

        #Setting the image to the label (masked to visible pixels)
        self._set_masked_pixmap(scaled)

        #Showing the label
        self.label.show()

        #self.setStyleSheet("border: 2px solid red;")  # DEBUG

        #Position and animation if its the first time spawning the instance
        if(self.first == True):
            print("reached")
            self.first = False
            self.move(posinicialx, 0)
            self.show()
            self.fall_animation() #Start with the fall animation
        
        #print("label:", self.label.size(), "pixmap:", scaled.size()) // DEBUG

    def mousePressEvent(self, e):
        if(not self.onanimation):
            if(e.button() == Qt.MouseButton.LeftButton):
                self.drag = True
                self.drag_history = [] # Start fresh velocity samples for throw physics
                self.offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft() # Put the cursor at the center of the widget
                
                #Set timer to activate shake animation
                self.held_timer = QTimer()
                self.held_timer.setSingleShot(True)
                self.held_timer.start(4500)
                self.start_shake = False
                self.held_timer.timeout.connect(lambda: setattr(self, 'start_shake', True))

                #Set image when timer ends

                self.held_timer.timeout.connect(lambda: self.setLabelImage(self.shaken_image))

                grab_pool = self.grab_sound_pool()
                if(grab_pool and not self.mutesounds):
                    sound_source = random.choice(grab_pool) # Choose a random "grabbed" sfx or hurt line
                    print(sound_source)
                    self.grabplayer.stop()
                    self.grabplayer.setVolume(vol(1.0))
                    self.grabplayer.setSource(QUrl.fromLocalFile(sound_source))
                    self.grabplayer.play()
                        
    def mouseMoveEvent(self, e):
        if(self.onanimation == False): 
            if(self.drag and e.buttons() & Qt.MouseButton.LeftButton):
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                new_top_left = e.globalPosition().toPoint()-self.offset
                new_top_left = self.clamp_to_screen(new_top_left) # Ensures the character falls onto the taskbar
                self.move(new_top_left)

                #Record movement samples so we know the velocity at release
                self.drag_history.append((time.monotonic(), self.pos()))
                if(len(self.drag_history) > 8):
                    self.drag_history.pop(0)
                
                #ERRATIC MOVEMENT
                if(self.start_shake):
                    dirx = random.randint(0,10)
                    diry = random.randint(0,10)
                    self.move(new_top_left.x()+dirx, new_top_left.y()+diry)
                    pass
                else:
                    #If not shaken, set normal image.
                    if(self.chosen_grabbed_image == False):
                        self.grabbed_image = random.choice(self.sprites['grabbed'])
                        self.setLabelImage(self.grabbed_image)
                        self.chosen_grabbed_image = True
        else:
            self.unsetCursor()      

    def mouseReleaseEvent(self, e):
        if(e.button() == Qt.MouseButton.LeftButton and not self.onanimation):

            self.setCursor(Qt.CursorShape.ArrowCursor) # Set cursor to default
            self.drag = False

            self.held_timer.stop()
            self.start_shake = False
            self.chosen_grabbed_image = False

            #Thrown or dropped? Fast flick = physics throw, gentle release = classic fall
            vx, vy = self._release_velocity()
            if((vx*vx + vy*vy) ** 0.5 > 900.0):
                self.start_throw(vx, vy)
            else:
                self.fall_animation()

            #Stop the sound effects to avoid audio glitches
            
    def _release_velocity(self):
        #Velocity (px/s) from the last ~120ms of drag movement
        now = time.monotonic()
        history = [(t, p) for (t, p) in self.drag_history if now - t < 0.12]
        self.drag_history = []
        if(len(history) < 2):
            return (0.0, 0.0)
        t0, p0 = history[0]
        t1, p1 = history[-1]
        dt = t1 - t0
        if(dt <= 0):
            return (0.0, 0.0)
        return ((p1.x() - p0.x()) / dt, (p1.y() - p0.y()) / dt)

    def start_throw(self, vx: float, vy: float):
        print(f"{self.name} thrown at ({vx:.0f}, {vy:.0f}) px/s")
        self.onanimation = True
        CAP = 3000.0
        THROW_SCALE = 0.8 # soften throws slightly below the raw flick speed
        vx *= THROW_SCALE
        vy *= THROW_SCALE
        self.velocity = [max(-CAP, min(CAP, vx)), max(-CAP, min(CAP, vy))]
        self.pos_f = [float(self.pos().x()), float(self.pos().y())]
        self.bounced_voice = False # one "sorry!" per flight, not one per wall
        self.setLabelImage(random.choice(self.sprites["falling"]))
        self.physics_timer.start(16)

    def _physics_step(self):
        DT = 0.016
        GRAVITY = 3200.0      # px/s^2
        BOUNCE_WALL = 0.5     # energy kept on wall bounce
        BOUNCE_FLOOR = 0.38   # energy kept on floor bounce
        FRICTION = 0.65       # horizontal damping per floor bounce
        MIN_BOUNCE = 700.0    # slower than this and we land instead of bouncing

        self.velocity[1] += GRAVITY * DT
        self.pos_f[0] += self.velocity[0] * DT
        self.pos_f[1] += self.velocity[1] * DT

        scr = QGuiApplication.screenAt(self.pos()) or self.windowHandle().screen()
        rect = scr.availableGeometry()
        floor_y = rect.bottom() - self.height() + 1
        right_x = rect.right() - self.width() + 5

        if(self.pos_f[0] < rect.left()):
            self.pos_f[0] = rect.left()
            self.velocity[0] = -self.velocity[0] * BOUNCE_WALL
            self._wall_bounce_voice()
        elif(self.pos_f[0] > right_x):
            self.pos_f[0] = right_x
            self.velocity[0] = -self.velocity[0] * BOUNCE_WALL
            self._wall_bounce_voice()
        if(self.pos_f[1] < rect.top()):
            self.pos_f[1] = rect.top()
            self.velocity[1] = -self.velocity[1] * 0.4

        if(self.pos_f[1] >= floor_y):
            impact = self.velocity[1]
            self.pos_f[1] = floor_y
            if(impact > MIN_BOUNCE):
                self.velocity[1] = -impact * BOUNCE_FLOOR
                self.velocity[0] *= FRICTION
            else:
                self._end_throw(impact)
                return

        self.move(int(self.pos_f[0]), int(self.pos_f[1]))

    def _wall_bounce_voice(self):
        #Clipped a screen edge mid-flight: apologise, but only for the first wall
        #of a given throw — a fast flick can bounce several times in a second.
        if(self.bounced_voice):
            return
        self.bounced_voice = True
        self.play_voice(*VOICE_POOLS["bounce"], ignore_chance=True)

    def _end_throw(self, impact_speed: float):
        self.physics_timer.stop()
        self.move(int(self.pos_f[0]), int(self.pos_f[1]))
        self.onanimation = False
        #Hard landings leave them briefly crashed on the ground
        if(impact_speed > 400):
            self.setLabelImage(resource_path(f'assets/{self.name}/sprites/fallingend.png'))
            self.play_voice(*VOICE_POOLS["crash"], ignore_chance=True) # you threw them; they get to react
            QTimer.singleShot(900, self.setDefaultLabel)
        else:
            self.setDefaultLabel()

    def clamp_to_screen(self, pt: QPoint) -> QPoint:
        # Gets actual screen or the one under the cursor
        scr = QGuiApplication.screenAt(pt) or self.windowHandle().screen()
        rect = scr.availableGeometry()  # Ignores the task bar space

        x = max(rect.left(),  min(pt.x(), rect.right()  - self.width()  + 5))
        y = max(rect.top(),   min(pt.y(), rect.bottom() - self.height() + 1))
        return QPoint(x, y)       

    
    def getName(self):
        return self.name
    
    def getLabel(self):
        return self.label
    
    def _set_masked_pixmap(self, pix: QPixmap):
        #Set the sprite AND shape the window to its visible pixels, so the
        #transparent margins around the character never block clicks meant
        #for the windows underneath.
        self.label.setPixmap(pix)
        self.label.resize(pix.size())
        self.resize(pix.size())
        mask = pix.mask()
        if(not mask.isNull()):
            self.setMask(mask)

    def setLabelImage(self, dir):
        pix = QPixmap(dir)

        scaled = pix.scaled(self.char_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation )

        self._set_masked_pixmap(scaled)
        self.label.repaint()

    def setDefaultLabel(self):
        spawn_sprite = random.choice(self.sprites["spawn"])
        self._set_masked_pixmap(spawn_sprite)



    def setAssociatedStopButton(self, b: QAction):
        self.associated_stop_button = b
    def setAssociatedPlayButton(self, b:QAction):
        self.associated_play_button = b
    def getTimer(self):
        return self.frame_timer
    def mute(self, flag: bool):
        self.mutesounds = flag
    def setOnAnimation(self):
        self.onanimation = not self.onanimation
    def getOnAnimation(self):
        return self.onanimation
    def fall_animation(self):
        
        self.onanimation = True # Warns click events that this widget is on an animation and it cant be clicked.

        #Get current position
        current_pos = self.pos()
        
        #Get the target height destination 
        y = self.clamp_to_screen(QPoint(0, get_size().height()-self.label.pixmap().size().height())).y() # Ensures that it falls onto the taskbar

        #Length of animation
        time = (get_size().height()-current_pos.y()) # Formula for time to fall is (size of screen - current y pos)
        time = int(1.5*time) # Convert to int to avoid bugs

        #Set the falling sprite
        falling_sprite = random.choice(self.sprites["falling"])
        
        self.setLabelImage(falling_sprite)

        #Deciding sprite for animation's end
        chance = random.randint(0,100)
        fallingend_sprite_dir = resource_path(f'assets/{self.name}/sprites/fallingend.png')
        
        #Animation 
        self.animation = QPropertyAnimation(self, b"pos")
        self.animation.setDuration(time) # Time it takes the animation to ble completed
        self.animation.setTargetObject(self) # Widget as the target for the animation
        self.animation.setStartValue(current_pos) # Current pos as start
        self.animation.setEndValue(QPoint(current_pos.x(), y)) # Same x coord, right above the taskbar as final frame
        self.animation.start() 
        
        #Setting a timer to go back to normal sprite
        self.timer = QTimer()
        self.timer.setSingleShot(True) #Only once
        self.timer.start(time) # Same time as animation length
        self.timer.timeout.connect(self.setOnAnimation) #Set OnAnimation bool back to false

        if(chance <=30):  #30% for the character to crash onto the ground, 70% for it to land properly
            self.timer.timeout.connect(lambda: self.setLabelImage(fallingend_sprite_dir))# When it ends, go back to normal sprite
        else:
            self.timer.timeout.connect(lambda: self.setDefaultLabel())
        
#A co-animation "set" is one shared moment two or more pets play together. Each
#entry is fully data-driven so new clips are add-a-dict, not edit-the-class:
#  participants  character names that must all overlap for the touch to fire, and
#                who is hidden/revealed around the clip. Order = left->right on end.
#  frames_dir    folder under assets/coanimations/ holding {n}.png frames.
#  sound         wav under assets/coanimations/sounds/.
#  width_scale/  co-art bounding box as a multiple of one pet's char_size (the art
#  height_scale  is scaled with KeepAspectRatio into this box).
#  fps           nominal frame rate (loop-then-pose uses it directly; play-once
#                only falls back to it when the wav is missing or muted).
#  mode          "loop-then-pose": loop frames for walk_ms, then hold a random
#                pose for pose_ms (chiikawa+hachiware today).
#                "play-once": run every frame exactly once, timed so the last
#                frame lands on the end of the audio, then finish (tanuki trio).
#  poses         final-pose stickers for loop-then-pose (ignored by play-once).
#  walk_ms/pose_ms  loop-then-pose timing.
COANIM_SETS: dict[str, dict] = {
    "hc_walktogether": {
        "participants": ["chiikawa", "hachiware"],
        "frames_dir": "hc_walktogether",
        "sound": "together.wav",
        "width_scale": 1.9,
        "height_scale": 1.3,
        "fps": 9,
        "mode": "loop-then-pose",
        "poses": ["hc_heart.png", "hc_handholding.png"],
        "walk_ms": 3400,   # how long they stroll together
        "pose_ms": 1800,   # how long the final pose is held
    },
    "tanuki_trio": {
        "participants": ["chiikawa", "hachiware", "usagi"],
        "frames_dir": "tanuki_trio",
        "sound": "tanuki.wav",
        "width_scale": 2.8,
        "height_scale": 1.3,
        "fps": 9,          # nominal fallback only; real timing derives from the wav
        "mode": "play-once",
        "poses": [],
    },
    #Solo moment: Hachiware buzzes with excitement. Only two drawings exist in the
    #source (a clean pose and a jittered one trailing its own smear); the 28 frames
    #cycle them A,A,B, which is what makes the vibration read.
    "hachiware_look": {
        "participants": ["hachiware"],
        "frames_dir": "hachiware_look",
        "sound": "look.wav",   # not installed yet — awaiting Sara's ear; until then
        "width_scale": 1.0,    # the missing wav just means nominal-fps timing
        "height_scale": 1.0,
        "fps": 9,
        "mode": "play-once",
        "poses": [],
    },
    #Chiikawa's solo "dodo dodo da do" dance, cut from the dance meme (ep dance
    #clip). Two distinct ~7s periods of the same continuous dance: _dance is the
    #bouncy front/arms phase, _dance2 the profile-turn phase. Pale-on-pale shot,
    #so these were ink-matted (--matte ink) and the ground shadow removed to match
    #the shadow-free house style. The spiky dashes are Chiikawa's own excited-
    #shiver motion-smear, kept on purpose. Like hachiware_look, one-pet sets fire
    #on demand from the tray, not check_touch.
    "chiikawa_dance": {
        "participants": ["chiikawa"],
        "frames_dir": "chiikawa_dance",
        "sound": "chiikawa_dance.wav",
        "width_scale": 1.30,
        "height_scale": 1.05,
        "fps": 9,          # nominal fallback only; real timing derives from the wav
        "mode": "play-once",
        "poses": [],
    },
    "chiikawa_dance2": {
        "participants": ["chiikawa"],
        "frames_dir": "chiikawa_dance2",
        "sound": "chiikawa_dance2.wav",
        "width_scale": 1.15,
        "height_scale": 1.05,
        "fps": 9,          # nominal fallback only; real timing derives from the wav
        "mode": "play-once",
        "poses": [],
    },
}


def _wav_duration_ms(path: str):
    """Duration of a PCM WAV in ms, or None if it can't be read. Used to sync a
    play-once clip's frame timer to its audio so the last frame lands on the end
    of the sound."""
    try:
        with wave.open(path, 'rb') as w:
            frames = w.getnframes()
            rate = w.getframerate()
            if(rate <= 0):
                return None
            return int(frames * 1000 / rate)
    except (wave.Error, OSError, EOFError):
        return None


class CoAnimation(QWidget):
    """A shared 'touching' moment: the participating pets hide and this widget
    plays a co-animation set (see COANIM_SETS), then the pets reappear on the
    floor. Chiikawa+Hachiware loop-and-pose; the tanuki trio plays once through.

    Assets live under assets/coanimations/: a <frames_dir>/{n}.png folder, any
    pose stickers, and sounds/<sound>.
    """
    def __init__(self, set_name: str, participants: 'list[Character]'):
        super().__init__(parent=None)
        self.set_name = set_name
        self.spec = COANIM_SETS[set_name]
        self.participants = participants
        self.mode = self.spec.get("mode", "loop-then-pose")
        self.fps = self.spec.get("fps", 9)
        self.walk_ms = self.spec.get("walk_ms", 3400)
        self.pose_ms = self.spec.get("pose_ms", 1800)
        self.finished = False

        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowFlag(Qt.WindowType.Tool)
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)

        self.label = QLabel(parent=self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # co-art bounding box, sized off one pet and the set's scale factors
        char_size = participants[0].char_size
        self.co_size = QSize(int(char_size.width() * self.spec["width_scale"]),
                             int(char_size.height() * self.spec["height_scale"]))

        #Frames
        self.walk_frames: list[QPixmap] = []
        frames_dir = Path(asset_path(f'assets/coanimations/{self.spec["frames_dir"]}'))
        if frames_dir.exists():
            for f in sorted(frames_dir.glob('*.png'), key=lambda p: int(p.stem)):
                pix = QPixmap(str(f)).scaled(self.co_size, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)
                self.walk_frames.append(pix)

        #Final pose (random pick among the set's stickers; play-once sets have none)
        self.pose_pixmap = None
        poses = [p for p in self.spec.get("poses", [])
                 if Path(asset_path(f'assets/coanimations/{p}')).exists()]
        if poses:
            chosen = random.choice(poses)
            self.pose_pixmap = QPixmap(asset_path(f'assets/coanimations/{chosen}')).scaled(
                self.co_size, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            print(f"coanimation pose: {chosen}")

        #Sound
        self.co_sound = QSoundEffect()
        self.co_sound.setVolume(vol(0.5))
        self.co_sound.setLoopCount(1)
        sound_path = asset_path(f'assets/coanimations/sounds/{self.spec["sound"]}')
        self.sound_available = Path(sound_path).exists()
        self.sound_duration_ms = _wav_duration_ms(sound_path) if self.sound_available else None
        if self.sound_available:
            self.co_sound.setSource(QUrl.fromLocalFile(sound_path))

        self.frame_idx = 0
        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self._next_frame)
        self.walk_anim = None

    def participant_names(self) -> 'list[str]':
        return [c.getName() for c in self.participants]

    def _set_pix(self, pix: QPixmap):
        self.label.setPixmap(pix)
        self.label.resize(pix.size())
        self.resize(pix.size())
        mask = pix.mask()
        if(not mask.isNull()):
            self.setMask(mask)

    def start(self):
        #Start at the midpoint of the participating pets, snapped to the floor
        xs = [c.pos().x() for c in self.participants]
        mid_x = sum(xs) // len(xs)
        scr = QGuiApplication.screenAt(self.participants[0].pos()) or QGuiApplication.primaryScreen()
        rect = scr.availableGeometry()

        first = self.walk_frames[0] if self.walk_frames else self.pose_pixmap
        if first is None:
            print("coanimation assets missing — aborting")
            self._finish()
            return
        self._set_pix(first)

        x = max(rect.left(), min(mid_x, rect.right() - self.width()))
        y = rect.bottom() - self.height() + 1
        self.move(x, y)
        self.show()
        self.label.show()

        muted = muteall_flag or self.participants[0].mutesounds

        #The frames are FRONT-FACING and this is a LOCKED SHOT: the pets look at
        #the viewer and bob in place, with no left/right profile. There is no
        #"facing" that a horizontal mirror could point at a travel direction —
        #mirroring only swaps which pet is on which side. So any sideways slide
        #makes the stationary bouncing feet glide across the floor, which reads
        #as moonwalking regardless of direction (this is why the earlier
        #frame-mirroring attempt couldn't fix it). The tanuki clip is likewise a
        #stationary locked shot. So: never travel horizontally — hold the widget
        #still and just cycle frames.
        if self.mode == "play-once" and self.walk_frames:
            #Sync the frame timer to the audio so the final frame lands on the end
            #of the clip. Start the sound and the timer in the same tick.
            if self.sound_available and not muted and self.sound_duration_ms:
                interval = max(1, int(self.sound_duration_ms / len(self.walk_frames)))
                self.co_sound.play()
            else:
                interval = int(1000 / self.fps)   # missing/muted wav -> nominal fps
            self.frame_idx = 0
            self.frame_timer.start(interval)
        elif self.walk_frames:
            #loop-then-pose: bounce happily in place for the walk duration, then
            #settle into the final pose.
            if(not muted):
                self.co_sound.play()
            self.frame_timer.start(int(1000 / self.fps))
            QTimer.singleShot(self.walk_ms, self._show_pose)
        else:
            if(not muted):
                self.co_sound.play()
            self._show_pose()

    def _next_frame(self):
        if(not self.walk_frames):
            return
        if self.mode == "play-once":
            #Advance once through the frames; finish after the last frame has had
            #its interval on screen (which lands on the end of the audio).
            self.frame_idx += 1
            if(self.frame_idx >= len(self.walk_frames)):
                self._finish()
                return
            self._set_pix(self.walk_frames[self.frame_idx])
        else:
            self.frame_idx = (self.frame_idx + 1) % len(self.walk_frames)
            self._set_pix(self.walk_frames[self.frame_idx])

    def _show_pose(self):
        self.frame_timer.stop()
        if(self.pose_pixmap is not None):
            #Keep the widget's floor position while the pose (bigger art) shows
            old_bottom = self.pos().y() + self.height()
            old_center = self.pos().x() + self.width() // 2
            self._set_pix(self.pose_pixmap)
            self.move(old_center - self.width() // 2, old_bottom - self.height())
        QTimer.singleShot(self.pose_ms, self._finish)

    def _finish(self):
        if(self.finished):
            return
        self.finished = True
        self.frame_timer.stop()
        if(self.walk_anim is not None):
            self.walk_anim.stop()
        end_coanimation(self)

    def abort(self):
        self._finish()


current_coanim: 'CoAnimation | None' = None
last_coanim_end: float = 0.0


def _coanim_config(set_name: str):
    """(enabled, cooldown_min_s, cooldown_max_s) for a set. Cooldown is shared
    across sets; the enabled flag is per-set under coanimations.sets.<name>,
    falling back to a legacy top-level "enabled" then True."""
    cfg = config_data.get("coanimations", {})
    cd_min = cfg.get("cooldown_min_s", 60)
    cd_max = cfg.get("cooldown_max_s", 150)
    set_cfg = cfg.get("sets", {}).get(set_name, {})
    enabled = set_cfg.get("enabled", cfg.get("enabled", True))
    return enabled, cd_min, cd_max


_coanim_next_ok: float = 0.0


def _resolve_participants(set_name: str):
    """Return the live Character objects for a set's participants, or None if any
    is missing / dragging / mid-throw (i.e. not ready to start the moment)."""
    by_name = {c.getName(): c for c in characters if c is not None}
    pets = []
    for name in COANIM_SETS[set_name]["participants"]:
        c = by_name.get(name)
        if(c is None or c.drag or c.physics_timer.isActive()):
            return None
        pets.append(c)
    return pets


def check_touch():
    """Runs on a timer: when all of a set's participants overlap, play that
    together-moment. Cooldown keeps it special. The rarer 3-way tanuki set is
    checked BEFORE the pair, so it wins when all three overlap at once."""
    global current_coanim, _coanim_next_ok
    if(current_coanim is not None):
        return
    if(time.monotonic() < _coanim_next_ok):
        return

    for set_name in ("tanuki_trio", "hc_walktogether"):
        enabled, _cd_min, _cd_max = _coanim_config(set_name)
        if(not enabled):
            continue
        pets = _resolve_participants(set_name)
        if(pets is None):
            continue
        #Require a real overlap, not a graze: shrink every rect a bit, then
        #require all of them to mutually intersect.
        rects = [c.frameGeometry().adjusted(10, 10, -10, -10) for c in pets]
        if(all(rects[i].intersects(rects[j])
               for i in range(len(rects)) for j in range(i + 1, len(rects)))):
            start_coanimation(set_name, pets)
            return


def _pause_for_coanim(char: 'Character'):
    char.randomtimer.stop()
    char.frame_timer.stop()
    char.jump_frame_timer.stop()
    char.held_timer.stop()
    char.physics_timer.stop()
    if(char.animation is not None):
        char.animation.stop()
    char.stop_current_sound()
    char.onanimation = True  # block clicks while hidden
    char.hide()


def start_coanimation(set_name: str, participants: 'list[Character]', forced: bool = False):
    global current_coanim
    if(current_coanim is not None):
        return
    print(f"coanimation start: {set_name} ({'forced' if forced else 'touch'})")
    for char in participants:
        _pause_for_coanim(char)
    current_coanim = CoAnimation(set_name, participants)
    current_coanim.start()


def end_coanimation(co: 'CoAnimation'):
    global current_coanim, _coanim_next_ok
    _enabled, cd_min, cd_max = _coanim_config(co.set_name)
    pets = co.participants
    scr = QGuiApplication.screenAt(co.pos()) or QGuiApplication.primaryScreen()
    rect = scr.availableGeometry()
    center = co.pos().x() + co.width() // 2

    #Spread the pets evenly around the widget's center (a 60px gap reproduces the
    #old two-pet spacing exactly), each clamped to the screen rect. Order follows
    #the set's participant list = left->right.
    gap = 60
    widths = [c.width() for c in pets]
    total = sum(widths) + gap * (len(pets) - 1)
    x = center - total // 2
    for char, w in zip(pets, widths):
        try:
            cx = max(rect.left(), min(x, rect.right() - char.width()))
            char.move(cx, rect.bottom() - char.height() + 1)
            char.setDefaultLabel()
            char.onanimation = False
            char.show()
            char.randomtimer.start() # resume (don't use start_random_timer — it re-connects the signal)
        except RuntimeError:
            pass  # character was kicked mid-moment
        x += w + gap
    #One of the cast speaks for the moment they just shared. A dance set gets the
    #victory line instead of thanks — same "that was fun" beat, better word for it.
    try:
        speaker = random.choice(pets)
        speaker.play_voice(*(VOICE_POOLS["dance"] if "dance" in co.set_name else VOICE_POOLS["coanim"]))
    except RuntimeError:
        pass  # character was kicked mid-moment

    co.hide()
    co.deleteLater()
    current_coanim = None
    _coanim_next_ok = time.monotonic() + random.randint(int(cd_min), int(cd_max))
    print(f"coanimation done, next possible in {int(_coanim_next_ok - time.monotonic())}s")


def force_coanimation(set_name: str):
    """Tray action: play a co-animation set on demand, if its cast is present."""
    if(current_coanim is not None):
        return
    pets = _resolve_participants(set_name)
    if(pets is None):
        names = ", ".join(n.capitalize() for n in COANIM_SETS[set_name]["participants"])
        yaha_tray.showMessage('Wait!', f'Spawn {names} first!',
                              QSystemTrayIcon.MessageIcon.Information, 500)
        return
    start_coanimation(set_name, pets, forced=True)


def create_character(name: str):
    
    name = name.lower() # lowercase to avoid compiling issues      
    if(name not in characters_names):
         
        characters_names.append(name) # Add to character list
        play_animation_menu.setDisabled(False) # Activate the animation menu
        kick_menu.setDisabled(False) # Enable kicking the character
        
        #Instance the character
        character = Character(name, get_size_for_characters())
        character.mute(muteall_flag) # Respect Mute All for characters spawned after muting

        ###Preload all animations
        yaha_tray.showMessage(f'Loading {name}', 'This may take a while the first time', QSystemTrayIcon.MessageIcon.Information, 500)
        character.preload_allanimations()
        character.set_sprite(resource_path(f'assets/{name}/sprites/spawn.png'))

        
        character.play_animsound("spawn") #Play the spawn animation sound for the corresponding character
        #...then speak, a beat later so the two sounds don't collide. The very
        #first pet of the session announces the app with a match-start line.
        global session_started
        pool = VOICE_POOLS["spawn"] if session_started else VOICE_POOLS["launch"]
        session_started = True
        QTimer.singleShot(500, lambda: play_voice_later(character, *pool))

        characters.append(character) #Add character to alive widgets list

        ###Set up related menus 
        #Stop animation Button
        stopanimationbutton = QAction(name) # Create a stop animation button for this character
        stop_animation_menu.addAction(stopanimationbutton) # Add the action to the corresponding menu
        stopanimationbutton.triggered.connect(lambda: character.blockAnimations()) # Set up button to block random animations
        character.setAssociatedStopButton(stopanimationbutton) # Set the button to the character
        
        #Play Animation Button
        playanimationbutton = QMenu(name) # Create a play animation menu for this character
        play_animation_menu.addMenu(playanimationbutton)
        for anim in totalanimations[name]:
            playanimationbutton.addAction(anim)
            
        
        playanimationbutton.triggered.connect(lambda action: character.start_anim(action.text()))
        character.setAssociatedPlayButton(playanimationbutton)

        kick_menu.addAction(name) #enable kicking the character
        muteall_button.setDisabled(False) # Enable the muteall button
        stop_animation_menu.setDisabled(False) # Enable the stop animations menu
        refresh_spawn_state() # grey out this character in the Spawn menu / Spawn All


#Setting up variables

app = QApplication([]) 

#Config — a per-user config (in the OS app-support folder) takes priority,
#then a config.json next to the app. Supports per-character overrides:
#  {"usagi": {"sound_chance": 0.75, "animation_interval_scale": 1.33,
#             "animations": {"dance": {"fps": 40}}}}
#sound_chance: probability (0-1) an animation sound actually plays.
#animation_interval_scale: multiplier on time between random animations.
#Co-animations (the shared touch-moments) are configured under "coanimations":
#  {"coanimations": {"cooldown_min_s": 60, "cooldown_max_s": 150,
#                    "sets": {"tanuki_trio": {"enabled": false}}}}
#cooldown_min_s/max_s: shared min/max seconds between any auto-triggered moment.
#sets.<name>.enabled: per-set toggle for the auto (touch) trigger; defaults True.
#Voice lines are on by default and switch off with a top-level {"voice_lines":
#false} (mirrored by the Voice Lines menu item), or per character with
#{"usagi": {"voice_lines": false}}.
config_data = {}
_config_candidates = [
    os.path.join(Path.home(), "Library", "Application Support", "Yaha-Pet", "config.json"),
    resource_path("config.json"),
]
for config_file in _config_candidates:
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        print(f"Loaded config from {config_file}")
        break
    except json.JSONDecodeError:
        print(f"ERROR: Badly written JSON in {config_file}")
    except FileNotFoundError:
        continue
else:
    print("No config file found, using defaults")

voice_lines_flag = bool(config_data.get("voice_lines", True))
chattiness = float(config_data.get("chattiness", chattiness))
master_volume = float(config_data.get("volume", master_volume))

#Setting the window and flags
yahawindow = QWidget()
yahawindow.setWindowFlag(Qt.WindowType.FramelessWindowHint) #  No title bar
yahawindow.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint) # Always on top
yahawindow.setWindowFlag(Qt.WindowType.Tool) # No taskbar icon
app.setQuitOnLastWindowClosed(False)

#Setting the window size
resize_to_current_screen()

#Attributes
yahawindow.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True) # Transparency: True
yahawindow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True) # Never intercept clicks meant for other apps

#Tray and icon
icon_path = resource_path('assets/usagi/icons/usagi.ico')
yaha_icon = QIcon(icon_path)
yaha_tray = QSystemTrayIcon(yaha_icon,parent=app)
yaha_tray.show()
#UPDATE CHECK
#
#Yaha-Pet has no auto-updater: this only ASKS GitHub what the newest release is
#and offers to open the download page. Everything here is best-effort and must
#never be able to break the app - it runs on other people's machines, over a
#network that may be missing, slow, captive-portalled or rate-limiting us. So:
#a background thread (never block the UI), a hard timeout, and a bare except
#that turns any failure into a quiet log line unless the user asked in person.
UPDATE_REPO = "ssskay/Yaha-Pet"
UPDATE_API = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
UPDATE_PAGE = f"https://github.com/{UPDATE_REPO}/releases/latest"

def _version_tuple(text: str):
    #Release tags in this repo look like "v1.2-macos", so the patch component is
    #optional and any suffix is ignored: "v1.2-macos" -> (1, 2, 0),
    #"1.2.3-beta" -> (1, 2, 3). Unparseable -> None.
    match = re.match(r'v?(\d+)\.(\d+)(?:\.(\d+))?', str(text).strip())
    if(match is None):
        return None
    return tuple(int(g or 0) for g in match.groups())

class UpdateCheck(QThread):
    """One GitHub API call, off the UI thread. Emits (latest_tag, url, error) -
    an empty error means the first two are good."""
    done = pyqtSignal(str, str, str)

    def run(self):
        request = urllib.request.Request(UPDATE_API, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Yaha-Pet/{YAHA_VERSION}"})
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
            tag = str(data.get("tag_name") or "")
            if(_version_tuple(tag) is None):
                self.done.emit("", "", "GitHub returned no usable version number")
                return
            self.done.emit(tag, str(data.get("html_url") or UPDATE_PAGE), "")
        except urllib.error.HTTPError as e:
            hint = " (rate limited — try again later)" if e.code in (403, 429) else ""
            self.done.emit("", "", f"GitHub replied {e.code}{hint}")
        except Exception as e:
            #Timeouts, DNS failures, TLS errors, captive portals, bad JSON. All
            #of it is "no answer right now", none of it is worth a crash.
            self.done.emit("", "", f"{type(e).__name__}: {e}")

def _update_decision(latest: str, error: str, current: str):
    """Pure decision step, split out from the dialogs so it can be tested:
    returns ("error"|"current"|"newer"|"unknown", detail)."""
    if(error):
        return ("error", error)
    newer, mine = _version_tuple(latest), _version_tuple(current)
    if(newer is None or mine is None):
        return ("unknown", latest)
    return ("newer", latest) if newer > mine else ("current", latest)

update_thread = None # kept alive; a GC'd QThread takes the signal with it

def check_for_updates(manual: bool = False):
    global update_thread
    if(update_thread is not None and update_thread.isRunning()):
        return
    update_thread = UpdateCheck()
    update_thread.done.connect(
        lambda latest, url, err: _show_update_result(latest, url, err, manual))
    update_thread.start()

def _show_update_result(latest: str, url: str, error: str, manual: bool):
    verdict, detail = _update_decision(latest, error, YAHA_VERSION)
    if(verdict in ("error", "unknown")):
        #A silent startup check that fails stays silent - no popup on a laptop
        #that just happens to be offline.
        if(manual):
            QMessageBox.information(None, "Check for Updates",
                                    f"Couldn't check right now.\n\n{detail}")
        else:
            print(f"update check: {detail}")
        return
    if(verdict == "newer"):
        box = QMessageBox()
        box.setWindowTitle("Update available")
        box.setIconPixmap(yaha_icon.pixmap(64, 64))
        box.setText(f"<b>Yaha-Pet {detail}</b> is out.")
        box.setInformativeText(f"You have {YAHA_VERSION}. Open the download page?")
        box.setStandardButtons(QMessageBox.StandardButton.Open |
                               QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Open)
        if(box.exec() == QMessageBox.StandardButton.Open):
            QDesktopServices.openUrl(QUrl(url or UPDATE_PAGE))
    elif(manual):
        QMessageBox.information(None, "Check for Updates",
                                f"You're on the latest version ({YAHA_VERSION}).")

tray_menu = QMenu()

##Context Menu actions
#Create character. Keep a handle on each spawn action (keyed by lowercase name)
#so refresh_spawn_state() can grey out characters already on screen and re-enable
#them when kicked - clearer than the old "already spawned" error.
character_list_menu = QMenu("Spawn Character")
spawn_actions : dict = {}
for _label in ("Usagi", "Hachiware", "Chiikawa"):
    spawn_actions[_label.lower()] = character_list_menu.addAction(_label)
character_list_menu.triggered.connect(lambda action: create_character(action.text()))
tray_menu.addMenu(character_list_menu)

#Spawn All: spawn everyone not already out. Greys out once all are on screen.
spawn_all_action = QAction("Spawn All")
spawn_all_action.triggered.connect(lambda: spawn_all())
tray_menu.addAction(spawn_all_action)

#Play animation
play_animation_menu = QMenu("Play Animation")
play_animation_menu.setDisabled(True)
tray_menu.addMenu(play_animation_menu)#Adding the play animation menu to the main menu

#Say hi
hi_action = tray_menu.addAction("Say hi!")
hi_action.triggered.connect(say_hi_message)

#Chiikawa + Hachiware together-moment on demand
together_action = tray_menu.addAction("Bring them together!")
together_action.triggered.connect(lambda: force_coanimation("hc_walktogether"))

#Chiikawa + Hachiware + Usagi tanuki trio moment on demand
tanuki_action = tray_menu.addAction("Tanuki time!")
tanuki_action.triggered.connect(lambda: force_coanimation("tanuki_trio"))

#Hachiware's solo excited-buzz moment on demand (not in check_touch: a one-pet
#set has no one to touch, so on-demand is its only trigger for now)
look_action = tray_menu.addAction("Look!")
look_action.triggered.connect(lambda: force_coanimation("hachiware_look"))

#Chiikawa's solo "dodo dodo da do" dance moments on demand (two dance phases;
#one-pet sets have no one to touch, so on-demand is their only trigger)
dance_action = tray_menu.addAction("Chiikawa dance!")
dance_action.triggered.connect(lambda: force_coanimation("chiikawa_dance"))
dance2_action = tray_menu.addAction("Chiikawa dance 2!")
dance2_action.triggered.connect(lambda: force_coanimation("chiikawa_dance2"))

#Local-pack moments only show up in the menu when their frames are installed.
for _act, _set in ((look_action, "hachiware_look"), (dance_action, "chiikawa_dance"), (dance2_action, "chiikawa_dance2")):
    _act.setVisible(coanim_available(_set))
    if not _act.isVisible():
        print(f"local pack: {_set} not installed, hiding its menu item")

#Kick out a character
kick_menu = QMenu("Kick")
kick_menu.triggered.connect(lambda action: kick_character(action))
kick_menu.setDisabled(True)
tray_menu.addMenu(kick_menu)

#Mute and mute all sounds
muteall_button = QAction("Mute All")
muteall_flag : bool = False
muteall_button.setDisabled(True)
tray_menu.addAction(muteall_button)
muteall_button.triggered.connect(lambda: mute_character('all'))

#Voice lines on/off. Checkable so the menu shows the current state; starts from
#the config's "voice_lines" key and applies live to everyone on screen.
voice_lines_action = QAction("Voice Lines")
voice_lines_action.setCheckable(True)
voice_lines_action.setChecked(voice_lines_flag)
voice_lines_action.toggled.connect(toggle_voice_lines)
tray_menu.addAction(voice_lines_action)

#How often they make ambient noise, and how loud everything is. Both are radio
#groups so the menu always shows the current setting, and both persist.
def _level_menu(title: str, levels: list, current: float, apply):
    menu = QMenu(title)
    group = QActionGroup(menu)
    group.setExclusive(True)
    for label, value in levels:
        act = QAction(label, menu)
        act.setCheckable(True)
        act.setChecked(abs(value - current) < 0.001)
        act.triggered.connect(lambda _checked=False, v=value: apply(v))
        group.addAction(act)
        menu.addAction(act)
    return menu

chattiness_menu = _level_menu("Chattiness", CHATTINESS_LEVELS, chattiness, set_chattiness)
volume_menu = _level_menu("Volume", VOLUME_LEVELS, master_volume, set_volume)
tray_menu.addMenu(chattiness_menu)
tray_menu.addMenu(volume_menu)

#Stop animation menu
stop_animation_menu = QMenu("Stop/Resume Random Animations of...")
stop_animation_menu.setDisabled(True)
tray_menu.addMenu(stop_animation_menu)

#Check for updates, by hand. macOS convention puts this in the app menu, which
#ApplicationSpecificRole achieves on the menu bar copy.
update_action = QAction("Check for Updates…")
update_action.triggered.connect(lambda: check_for_updates(manual=True))
tray_menu.addAction(update_action)

#Quit - Must always be last 
exit_action = tray_menu.addAction("Exit")
exit_action.triggered.connect(close_app)


#Set the menu to the tray
yaha_tray.setContextMenu(tray_menu)

#macOS: also attach the menu to the Dock icon (right-click / click-and-hold),
#since crowded menu bars can hide the tray icon.
try:
    tray_menu.setAsDockMenu()
    print("Dock menu attached")
except AttributeError:
    print("setAsDockMenu not available on this platform")

#macOS menu bar: mirror the same commands into a native top-of-screen menu bar so
#they're discoverable without hunting for the tray/dock icon. We reuse the SAME
#QAction/QMenu objects the tray uses, so dynamic state (submenus populated on
#spawn, enabled/disabled) stays in sync automatically - single source of truth.
YAHA_VERSION = "1.3"  # keep in sync with Yaha-Pet.spec BUNDLE version and the
                      # GitHub release tag (v1.3-macos), which the update check reads

def show_about():
    box = QMessageBox()
    box.setWindowTitle("About Yaha-Pet")
    box.setIconPixmap(yaha_icon.pixmap(96, 96))
    box.setText(f"<b>Yaha-Pet</b> {YAHA_VERSION}")
    box.setInformativeText(
        "A desktop pet starring Usagi, Chiikawa, and Hachiware.\n\n"
        "Spawn a character from the Characters menu, then use the Actions menu to "
        "make them say hi, dance, or bring Chiikawa and Hachiware together.\n\n"
        "Made with love · me.sarakay.YahaPet"
    )
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()

menu_bar = QMenuBar()  # parentless -> becomes the application-wide menu bar on macOS

#About -> macOS relocates AboutRole actions into the "Yaha-Pet" application menu.
about_action = QAction("About Yaha-Pet")
about_action.setMenuRole(QAction.MenuRole.AboutRole)
about_action.triggered.connect(show_about)
menu_bar.addAction(about_action)
update_action.setMenuRole(QAction.MenuRole.ApplicationSpecificRole)
menu_bar.addAction(update_action)

#Characters menu - spawning and per-character controls.
characters_menu = menu_bar.addMenu("Characters")
characters_menu.addAction(spawn_all_action)    # Spawn All (shared with tray)
characters_menu.addMenu(character_list_menu)   # Spawn > (shared with tray)
characters_menu.addMenu(kick_menu)             # Kick > (shared, enables on spawn)
characters_menu.addSeparator()
characters_menu.addAction(muteall_button)      # Mute All (shared)
characters_menu.addAction(voice_lines_action)  # Voice Lines (shared)
characters_menu.addMenu(chattiness_menu)       # Chattiness > (shared)
characters_menu.addMenu(volume_menu)           # Volume > (shared)

#Actions menu - fun things the spawned characters can do (room for more easter
#eggs over time).
actions_menu = menu_bar.addMenu("Actions")
actions_menu.addAction(hi_action)              # Say hi! (shared)
actions_menu.addAction(together_action)        # Bring them together! (shared)
actions_menu.addAction(tanuki_action)          # Tanuki time! (shared)
actions_menu.addAction(look_action)            # Look! (shared)
actions_menu.addMenu(play_animation_menu)      # Play Animation > (shared)
actions_menu.addMenu(stop_animation_menu)      # Stop/Resume Random > (shared)

#Quit -> macOS relocates QuitRole into the application menu as Quit (Cmd-Q).
menu_bar.addAction(exit_action)
exit_action.setMenuRole(QAction.MenuRole.QuitRole)

#yahawindow.show() — disabled on macOS: this invisible fullscreen always-on-top
#window intercepted clicks meant for other applications (on Windows, layered
#windows pass clicks through transparent pixels automatically; macOS is less
#forgiving). Nothing is parented to it, so it doesn't need to be shown at all.

#Defining each animation and each character
totalanimations : dict[str, list[str]] = {}
allcharacters = ["usagi", "hachiware", "chiikawa"]
available_coop_animations : dict[str, list[str]] = {}

#Declaring variables for future use and keeping them alive from garbage collection
sound = QSoundEffect()
farewell_sound = QSoundEffect() # outlives the character it says goodbye for
characters = [] # List to hold character instances
characters_names = [] # List to hold current alive characters
CHAR_CODES : dict[str, str] = {"usagi": "u", "hachiware": "h", "chiikawa": "c"}

#VOICE HELPERS
def play_voice_later(char: 'Character', *labels: str):
    #Deferred voice line, tolerating the pet being kicked before the timer fires.
    try:
        char.play_voice(*labels)
    except RuntimeError:
        pass

def play_farewell(char: 'Character'):
    #Concede line on despawn. The character (and its own QSoundEffect) is deleted
    #immediately after being kicked, so this plays on a module-level player.
    if(char.mutesounds or muteall_flag or not voice_enabled(char.getName())):
        return
    paths = [char.voice_lines[label] for label in VOICE_POOLS["despawn"]
             if label in char.voice_lines]
    if(not paths):
        return
    farewell_sound.setVolume(vol(0.6))
    farewell_sound.setLoopCount(1)
    farewell_sound.setSource(QUrl.fromLocalFile(random.choice(paths)))
    farewell_sound.play()

#MENU FUNCTIONS
def mute_character(name: str):
    global muteall_flag
    muteall_flag = not muteall_flag
    if(name =='all'):
        for character in characters:
            if(character != None):
                character.mute(muteall_flag)
        #Reflect the state on the menu item so it works as a toggle
        muteall_button.setText("Unmute All" if muteall_flag else "Mute All")
    
def spawn_all():
    #Spawn every character not already on screen.
    for name in allcharacters:
        if(name not in characters_names):
            create_character(name)

def refresh_spawn_state():
    #Grey out characters already on screen in the Spawn menu, and disable Spawn
    #All once everyone is out. Shared QAction objects update tray + menu bar at once.
    for name, action in spawn_actions.items():
        action.setDisabled(name in characters_names)
    spawn_all_action.setDisabled(len(characters_names) >= len(allcharacters))

def kick_character(action: QAction):
    charactername = action.text()
    if(current_coanim is not None and charactername in current_coanim.participant_names()):
        current_coanim.abort() # release the participating pets before kicking one

    characters_names.remove(charactername)
    index = 0
    
    for target in characters: # For every character in alive characters list
        if(target != None):
            if(target.getName() == charactername):
                play_farewell(target) # must fire before the widget (and its player) dies
                characters.remove(target)
                target.setAssociatedStopButton(None)
                target.setAssociatedPlayButton(None)
                target.deleteLater()
                del target
                break
        index+=1
    kick_menu.removeAction(action)
    if(not kick_menu.actions()): #iF THERE ARENT ANY ACTIONS, THERE ARENT ANY CHARACTERS ALIVE, DISABLE ALL RELEVANT MENUS
        kick_menu.setDisabled(True)
        play_animation_menu.setDisabled(True)
        muteall_button.setDisabled(True)
        stop_animation_menu.setDisabled(True)
    refresh_spawn_state() # this character can be spawned again now

def setup_all_menus():
    #Let the user know that the app has been initialized
    yaha_tray.showMessage('Una!','App started, check your Windows System Tray and right click it to start!', yaha_icon, 500)
    global muteall_flag
    muteall_flag = False
    if(not totalanimations):
        
        for character in allcharacters:
            character_animations = []
            dir = Path(resource_path(f'assets/{character}/animations'))
            if(Path.exists(dir)):
                for folder_name in dir.iterdir():
                    print(folder_name)
                    folder_name = folder_name.name
                    print(folder_name)
                    character_animations.append(folder_name)
            totalanimations[character] = character_animations

            coop_dir = Path(resource_path(f'assets/coanimations'))
            
            if(Path.exists(coop_dir)):
                coop_animations = []
                for folder_name in coop_dir.iterdir():
                    folder_name = folder_name.name
                    
                    if(character[0] == folder_name[0] or character[0] == folder_name[1]): 
                        #The first and second letter of each coanimation file starts with the character's initial
                        #For example, "hc.png" is a coanimation sprite of H-achiware and C-hiikawa.
                        coop_animations.append(folder_name)
            else:
                available_coop_animations[character] = []
            available_coop_animations[character] = coop_animations
            #print(available_coop_animations[character])
        
    #print(totalanimations["hachiware"])
     
           
 
#Start event loop
setup_all_menus()

#Auto-spawn Usagi on startup so opening the app visibly does something.
#(On macOS the app lives in the menu bar only, which is easy to miss.)
QTimer.singleShot(600, lambda: create_character("usagi"))

#Quiet update check a few seconds in, so it never competes with startup and
#never interrupts anyone who is offline. Opt out with {"check_updates": false}.
if(config_data.get("check_updates", True)):
    QTimer.singleShot(4000, lambda: check_for_updates(manual=False))

#Touch detection: chiikawa + hachiware meeting triggers their together-moment.
touch_timer = QTimer()
touch_timer.timeout.connect(check_touch)
touch_timer.start(500)

app.exec()


