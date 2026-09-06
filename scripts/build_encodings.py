# we need to create a memory converting each face into a numerical representation face encoding
# saving those in a dictionary to compare our picture against
# so when later when a webcam detects a face it will create a 128 no encoding and compare it w the stored encodings

import os  # allows python to work w folders and files on computers

import face_recognition  # will detect faces and generate 128dim face encodings

from attendance.enrolment import KNOWN_FACES_DIR, encoding_write_lock
from attendance.recognition import ENCODINGS_FILE, save_known_encodings

# this is where the training images and processed encodings will be stored,
# each person will have their own folder so allows u to store multiple photos for each individual
#
# BOTH paths are imported rather than written out again here, and the input one is the
# part that matters. Hardcoding the input as PROJECT_ROOT / "known_faces" while writing
# the env-aware ENCODINGS_FILE would make the two halves disagree: point KNOWN_FACES_DIR
# at persistent storage -- which is exactly what the README tells a deployment to do --
# and this script would scan the empty directory next to the source and write the
# resulting empty cache over the real one. A rebuild that deletes everything it was meant
# to rebuild.
knownfaces_dir = KNOWN_FACES_DIR
cache_file = ENCODINGS_FILE

def main():
    known_encodings = {} # dict where the key will be the person's name and the value will be the several face encodings for that person which will make recognition more reliable

    if not os.path.isdir(knownfaces_dir):
        # said plainly rather than left as a FileNotFoundError traceback: the usual cause
        # is KNOWN_FACES_DIR pointing somewhere the photos are not, and the fix is to set
        # it, which a stack trace does not suggest
        print(f"No photo directory at {knownfaces_dir}.")
        print("Set KNOWN_FACES_DIR if the photos live somewhere else.")
        raise SystemExit(1)

    for person_name in os.listdir(knownfaces_dir): # loops thru each person's folder in the directory
        person_dir = os.path.join(knownfaces_dir, person_name) #knownfaces/person_name os.path deals w directory paths
        if not os.path.isdir(person_dir): # if your path is not a directory skip  for example it's README.txt
            continue

        person_encodings = [] # stores encodings belonging to the current person

        for filename in os.listdir(person_dir):
            ext = os.path.splitext(filename)[1].lower() # splitting filename from extension and checking if the image is any one of the types mentioned below
            # skip thru files that are not images
            if ext not in (".jpg", ".jpeg" , ".png"):
                continue

            path = os.path.join(person_dir, filename)
            image = face_recognition.load_image_file(path) #knownfaces/person_name/img1.jpg loading the image into memory as a numpy array
            encodings = face_recognition.face_encodings(image) # converting the image into a 128 dimensional encoding

            if len(encodings) != 1: # to validate if there is only one face detected if more than 1 skip after letting them know  that 2 faces in frame
                print(f"{filename} ({person_name}): expected 1 face, found {len(encodings)} - skipping")
                continue # skip on to next image

            # and then add your encoding  to your list of encodings for that person
            person_encodings.append(encodings[0])

        if person_encodings: # if the list has atleast one encoding
            known_encodings[person_name] = person_encodings # store it
            print(f"Loaded {person_name}: {len(person_encodings)} encodings")
        else:
            print(f"Warning: no usable photos for {person_name}")

    if not known_encodings:
        # A rebuild that found nobody is almost always a rebuild pointed at the wrong
        # directory, and writing the empty result would destroy every enrolled face --
        # silently, and in the one command an operator runs when something is already
        # wrong. There is no case where overwriting the cache with nothing is the wanted
        # outcome, so it is refused rather than confirmed.
        print(f"No usable photos found in {knownfaces_dir}.")
        print(f"Refusing to overwrite {cache_file} with an empty cache.")
        raise SystemExit(1)

    # now save everything, through save_known_encodings() rather than writing the file
    # here: the format then lives in one place, so this script and the enrolment page
    # cannot drift into writing different things.
    # the cache is a .npz rather than a pickle -- see attendance/recognition.py for why a
    # file the web application writes must not be one that executes on load.
    #
    # Under the same lock the enrolment page uses. This is a full overwrite, so running it
    # while the server is up would otherwise be able to land on top of somebody's
    # enrolment and undo it -- and this script is most likely to be run precisely when
    # somebody is fixing enrolment problems, which is when the server is busiest with them.
    with encoding_write_lock():
        save_known_encodings(known_encodings, cache_file)
    # now u dont have to read every image and recompute all of the encodings everytime the
    # program started, which makes the application start faster

    print(f"\nTotal people loaded: {len(known_encodings)}")
    print(f"Saved to {cache_file}")

# called cache bec it stores precomputed results


# wrapped in a function behind this guard like the other scripts. without it, importing
# this module for any reason would rescan every photo and overwrite the cache as a
# side effect -- which is exactly what the guard in recognise_live.py already prevents there.
if __name__ == "__main__":
    main()
