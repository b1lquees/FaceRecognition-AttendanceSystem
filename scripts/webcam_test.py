import cv2 # this gives us access to all of opencv's function opening webcam reading vid displaying windows drawing rectangles 
import face_recognition

# everything below only runs when this file is executed directly (python webcam_test.py)
# not when pytest or another file imports/discovers this module otherwise it would
# immediately try to open the webcam and crash pytest's collection (SystemExit from exit())
if __name__ == "__main__":
    vid_cap = cv2.VideoCapture(0) # this is a class constructor 0 means use the default camera . VideoCapture() creates an object that connects to a camera returns as a live connection w the camera which will be store in the variable cap 

    if not vid_cap.isOpened():
        print("Error: Could not open camera")
        exit() # later implement creation of a window which shows this instead of a text on terminal 

    while True: # will keep running once per frame  capturing detecting the face and displaying the updated frame 
        ret , frame = vid_cap.read() # this is an instance method which will return us the next frame from our video and also tell us whether it succeeded true or false and the frame . the frame is actually a numpy array
        if not ret: # bec ret boolean will store true or false hence if not true print this 
            print("Error: failed to grab frame")
            break
        # instead of detecting faces on the full sized webcam frame it first shrinks the image performs face detection on the smaller image (faster) and then scales it back to the original image size b4 drawifn the rectangles 
        # the face detection algorithm has much less data to process making it faster 
        small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
        rgb_small = small_frame[:,:, ::-1] # explained below 
        # detect faces on smaller image ( this is faster)
        face_locations = face_recognition.face_locations(rgb_small)
            # face_locations() is a function in face_recognition library which takes the rgb_frame webcame image as its input and analyses to find human faces in rgb format
            # it returns a list of coordinates of every face it detectes we geta tuple in the forn (top, right,bottom,left) which specifies the position 
            # we have stored this in the variable face_locations

        # for each tuple in the list use the rectangle function to draw a box on the image in frame 
        # 1st argument the imae on which the rectangle will be drawn
        # 2nd and 3rd the top left and bottom right corner definifn the size and position
        # fourth the rectangle colour whihc is green here
        # 4th thickness of the rectangle border in pixels
        for(top, right, bottom, left) in face_locations:
            # multiply the detected coordinates by 4 to convert them back to the original size 
            top *= 4; right *= 4; bottom *= 4; left *= 4
            cv2.rectangle(frame, (left, top), (right, bottom), (0,255,0),2)


        cv2.imshow("Video" , frame) # imshow displays the video will update the existing window rather than opening a new one and the frame variable passed is the image captured from camera
        if cv2.waitKey(1) & 0xFF == ord('q'): # waitkey waits for 1 millisecond for the keyboard key to be pressed if no key pressed turns -1 else returns the ascii code of that key
            # bitwise operator used here suppose we press q which is 113 it will be converted to binary and 01110001 and 11111111 are compared w each other using the and operator since every bit is 1 in 255 the original 
            # bits stay same and we get 113 in binary again this is compared with the q charcter on rhs inside the ord() function which returns the unicode pount for ascii
            break
    vid_cap.release() # releases the camera so other programs can use it example zoom teams another python script else the webcam may stay locked sometimes the webcam light stays on until program completely exits
    cv2.destroyAllWindows() # closes every window opencv created bec imshow does create a window webcam

#rgb_frame = frame[:,:, ::-1] opencv stores colours in the order bgr while face_recognition expects  rgb so we reverse the last dimension
# we re taking every row using the first : and every col using the 2nd : and reversing the colour channels using ::-1 
# now the frame is in the order expected by the f_r lib